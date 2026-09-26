import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.artifacts import write_once
from mcp_sorter.cli import app
from mcp_sorter.events import record_event
from mcp_sorter.jobs import JobRequest, claim, enqueue, execute, finish, get_job, renew, run_once
from mcp_sorter.operations import status
from mcp_sorter.settings import Settings


def request(key="once", kind="evaluation", profile="baseline"):
    return JobRequest(idempotency_key=key, kind=kind, profile=profile)


def test_concurrent_submission_and_claims_are_idempotent(runtime):
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: enqueue(runtime, request()), range(8)))
        claims = list(pool.map(lambda _: claim(runtime.engine), range(8)))
    assert len({j["id"] for j in jobs}) == 1
    assert len([j for j in claims if j]) == 1
    with pytest.raises(ValueError, match="different"):
        enqueue(runtime, request(profile="demo-fast"))
    with pytest.raises(ValueError, match="not found"):
        get_job(runtime.engine, "missing")


def test_expired_worker_cannot_commit_or_renew_after_reclaim(runtime):
    enqueue(runtime, request())
    now = time.time()
    old = claim(runtime.engine, now)
    assert renew(runtime.engine, old, now + 10)
    assert claim(runtime.engine, now + 31) is None
    new = claim(runtime.engine, now + 41)
    assert new["id"] == old["id"] and new["owner"] != old["owner"]
    assert not finish(runtime, old, {"report_id": "stale"}, now=now + 42)
    assert not renew(runtime.engine, old, now + 42)
    assert finish(runtime, new, {"report_id": "current"}, now=now + 42)
    assert get_job(runtime.engine, old["id"])["status"] == "completed"


def test_job_is_recovered_after_worker_process_exits(runtime):
    queued = enqueue(runtime, request())
    script = (
        "import os; from mcp_sorter.runtime import Runtime; "
        "from mcp_sorter.settings import Settings; from mcp_sorter.jobs import claim; "
        "r=Runtime(Settings()); claim(r.engine); os._exit(17)"
    )
    child = subprocess.run(
        [sys.executable, "-c", script],
        timeout=30,
        env={**os.environ, "SORTER_DATA_DIR": str(runtime.settings.data_dir)},
    )
    assert child.returncode == 17
    assert get_job(runtime.engine, queued["id"])["status"] == "running"
    with runtime.engine.begin() as db:
        db.execute(text("UPDATE jobs SET lease_until=0"))
    assert run_once(runtime)
    recovered = get_job(runtime.engine, queued["id"])
    assert recovered["status"] == "completed" and recovered["attempts"] == 2


def test_slow_job_renews_its_lease(runtime, monkeypatch):
    queued = enqueue(runtime, request())
    observed = []

    def slow_execute(current, job):
        time.sleep(5.2)
        observed.append(get_job(current.engine, job["id"])["lease_until"] > job["lease_until"])
        return {"fixture": True}

    monkeypatch.setattr("mcp_sorter.jobs.execute", slow_execute)
    assert run_once(runtime)
    assert observed == [True]
    assert get_job(runtime.engine, queued["id"])["status"] == "completed"


def test_worker_survives_database_contention(runtime, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from mcp_sorter.jobs import Worker

    worker = Worker(runtime)

    def fail_once(_):
        worker.stopped.set()
        raise OperationalError("", {}, None)

    monkeypatch.setattr("mcp_sorter.jobs.run_once", fail_once)
    worker.run()


def test_retry_budget_and_deadline_alerts(runtime):
    job = enqueue(runtime, request())
    now = time.time()
    for attempt in range(3):
        current = claim(runtime.engine, now + attempt * 100)
        assert current["attempts"] == attempt + 1
    assert claim(runtime.engine, now + 400) is None
    assert get_job(runtime.engine, job["id"])["status"] == "failed"
    assert "retry budget" in status(runtime)["alerts"][0]
    another = enqueue(runtime, request("another"))
    claim(runtime.engine)
    with runtime.engine.begin() as db:
        db.execute(text("UPDATE jobs SET lease_until=0 WHERE id=:id"), {"id": another["id"]})
    assert any("lease expired" in alert for alert in status(runtime)["alerts"])


def test_pinned_evaluation_survives_refresh_and_report_reuse(runtime):
    job = enqueue(runtime, request())
    pinned = runtime.catalog.active()
    other = runtime.catalog.publish(runtime.catalog.records()[:2], "2026-09-03T00:00:00Z")
    runtime.catalog.activate(other)
    assert run_once(runtime)
    stored = get_job(runtime.engine, job["id"])
    assert stored["status"] == "completed"
    report = json.loads((runtime.settings.data_dir / "reports" / f"{job['id']}.json").read_text())
    assert report["provenance"]["snapshot"] == pinned
    assert execute(runtime, stored)["report_id"] == job["id"]
    assert not run_once(runtime)


def test_job_reuses_json_and_repairs_html_without_running_ranker(runtime, monkeypatch):
    from mcp_sorter.evaluation import report_path

    job = enqueue(runtime, request())
    execute(runtime, job)
    path = report_path(runtime.settings.data_dir, job["id"])
    original = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Saved evaluations must not run again")

    monkeypatch.setattr("mcp_sorter.jobs.evaluate", forbidden)
    path.with_suffix(".html").unlink()
    execute(runtime, job)
    assert path.with_suffix(".html").read_text().endswith("</html>")
    assert path.read_bytes() == original
    path.write_text("corrupted")
    with pytest.raises(ValueError):
        execute(runtime, job)


def test_racing_evaluation_writer_repairs_the_winning_report(runtime, monkeypatch):
    from mcp_sorter.evaluation import save_report

    job = enqueue(runtime, request())

    def other_writer(directory, report):
        save_report(directory, report)
        raise FileExistsError("Another worker already published this report")

    monkeypatch.setattr("mcp_sorter.jobs.save_report", other_writer)
    assert execute(runtime, job) == {"report_id": job["id"]}


def test_refresh_preserves_concurrent_operator_activation(runtime):
    enqueue(runtime, request(kind="catalog-refresh"))
    job = claim(runtime.engine)
    result = execute(runtime, job)
    assert result["snapshot"] != runtime.catalog.active()
    other = runtime.catalog.publish(runtime.catalog.records()[:2], "2026-09-03T00:00:00Z")
    runtime.catalog.activate(other)
    assert finish(runtime, job, result)
    assert runtime.catalog.active() == other


def test_refresh_and_bounded_retries_keep_previous_catalog(runtime, monkeypatch):
    job = enqueue(runtime, request(kind="catalog-refresh"))
    previous = runtime.catalog.active()
    assert run_once(runtime)
    assert runtime.catalog.active() != previous
    assert get_job(runtime.engine, job["id"])["status"] == "completed"
    failed = enqueue(runtime, request("failed", "catalog-refresh"))
    monkeypatch.setattr(
        "mcp_sorter.jobs.execute", lambda *a: (_ for _ in ()).throw(ValueError("secret"))
    )
    for _ in range(3):
        with runtime.engine.begin() as db:
            db.execute(text("UPDATE jobs SET available_at=0 WHERE id=:id"), {"id": failed["id"]})
        assert run_once(runtime)
    row = get_job(runtime.engine, failed["id"])
    assert row["status"] == "failed" and row["error_code"] == "execution-failed"
    assert "secret" not in json.dumps(status(runtime))


def test_live_refresh_uses_controlled_contract(runtime, monkeypatch):
    records = runtime.catalog.records()
    runtime.settings.mode = "live"
    enqueue(runtime, request(kind="catalog-refresh"))
    job = claim(runtime.engine)
    monkeypatch.setattr("mcp_sorter.jobs.fetch_registry", lambda _: (records, []))
    assert execute(runtime, job)["snapshot"]
    monkeypatch.setattr("mcp_sorter.jobs.fetch_registry", lambda _: (records, ["invalid"]))
    with pytest.raises(ValueError, match="Incomplete"):
        execute(runtime, job)


def test_atomic_artifact_and_event_retention(runtime, tmp_path):
    path = tmp_path / "artifact.json"
    write_once(path, "original")
    with pytest.raises(FileExistsError):
        write_once(path, "replacement")
    assert path.read_text() == "original" and not list(tmp_path.glob("*.tmp"))
    for _ in range(1002):
        record_event(runtime.engine, "job", "completed")
    with runtime.engine.connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM events")).scalar_one() == 1000


def test_operations_api_worker_metrics_and_comparison(tmp_path):
    application = create_app(Settings(data_dir=tmp_path))
    with TestClient(application) as http:
        job = http.post("/api/v1/jobs", json=request().model_dump()).json()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = http.get(f"/api/v1/jobs/{job['id']}").json()
            if row["status"] == "completed":
                break
            time.sleep(0.05)
        assert row["status"] == "completed"
        assert http.get("/api/v1/jobs/missing").status_code == 404
        assert (
            http.post("/api/v1/jobs", json=request(profile="demo-fast").model_dump()).status_code
            == 409
        )
        http.post("/api/v1/rankings", json={"query": "github", "profile": "demo-timeout"})
        info = http.get("/api/v1/operations").json()
        assert info["traces"] and info["alerts"] and info["jobs"]
        assert "sorter_requests_total" in http.get("/metrics").text
        assert application.state.telemetry.reader.get_metrics_data().resource_metrics
        assert "sorter_jobs" in http.get("/metrics").text
        assert http.post("/api/v1/rankings", content=b"x" * 1_048_577).status_code == 413
        assert http.get(
            "/api/v1/evaluation-comparison", params={"baseline": job["id"], "candidate": job["id"]}
        ).json()["passes_regression_gate"]
        assert (
            http.get(
                "/api/v1/evaluation-comparison",
                params={"baseline": "invalid", "candidate": "invalid"},
            ).status_code
            == 422
        )


def test_job_cli_without_background_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SORTER_WORKER_ENABLED", "false")
    runner = CliRunner()
    assert runner.invoke(app, ["jobs", "submit", "evaluation", "cli"]).exit_code == 0
    assert runner.invoke(app, ["jobs", "work"]).exit_code == 0
    assert "completed" in runner.invoke(app, ["status"]).stdout
    with TestClient(create_app(Settings(data_dir=tmp_path, worker_enabled=False))) as http:
        assert http.get("/api/v1/operations").json()["worker_enabled"] is False


def test_unhandled_errors_have_bounded_telemetry(tmp_path, caplog):
    application = create_app(Settings(data_dir=tmp_path, worker_enabled=False))

    @application.get("/failure")
    def fail():
        raise RuntimeError("confidential-provider-content")

    with (
        caplog.at_level("INFO", logger="mcp_sorter.requests"),
        TestClient(application, raise_server_exceptions=False) as http,
    ):
        assert http.get("/failure").status_code == 500
        assert 'status="500"' in http.get("/metrics").text
        assert "confidential" not in json.dumps(application.state.telemetry.spans.snapshot())
    assert "confidential" not in "".join(
        record.message for record in caplog.records if record.name == "mcp_sorter.requests"
    )
