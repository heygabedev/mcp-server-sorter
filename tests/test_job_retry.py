import json
from concurrent.futures import ThreadPoolExecutor
from time import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.cli import app
from mcp_sorter.job_retry import RetryRequest, retry_job
from mcp_sorter.jobs import JobRequest, claim, enqueue, finish, get_job, run_once
from mcp_sorter.versioning import Configuration, activate


def failed_job(runtime):
    queued = enqueue(
        runtime,
        JobRequest(
            kind="evaluation", dataset="challenge", profile="demo-fast", idempotency_key="original"
        ),
    )
    now = time()
    for index in range(3):
        current = claim(runtime.engine, now + index * 100)
        finish(runtime, current, {}, failed=True, now=now + index * 100, error_code="timeout")
    return get_job(runtime.engine, queued["id"])


def test_concurrent_retries_preserve_original_pins_and_attempts(runtime):
    failed = failed_job(runtime)
    original = json.loads(failed["payload"])
    snapshot = runtime.catalog.publish(runtime.catalog.records()[:2], "2026-10-12T00:00:00Z")
    configuration = runtime.configurations.publish(Configuration(name="Other", name_weight=8.0))
    activate(runtime.catalog, runtime.configurations, snapshot, configuration)
    request = RetryRequest(idempotency_key="retry")
    with ThreadPoolExecutor(max_workers=4) as workers:
        retried = list(workers.map(lambda _: retry_job(runtime, failed["id"], request), range(8)))
    assert len({job["id"] for job in retried}) == 1
    new = retried[0]
    assert new["parent_job_id"] == failed["id"] and new["payload"] == failed["payload"]
    assert new["attempts"] == 0 and new["attempt_history"] == []
    assert run_once(runtime)
    completed = get_job(runtime.engine, new["id"])
    assert completed["status"] == "completed"
    report = json.loads((runtime.settings.data_dir / "reports" / f"{new['id']}.json").read_text())
    assert report["dataset_sha256"] == original["dataset_sha256"]
    assert report["profile"] == "demo-fast"
    assert report["provenance"]["snapshot"] == original["snapshot"]
    assert report["provenance"]["configuration"] == original["configuration"]
    assert get_job(runtime.engine, failed["id"]) == failed
    assert retry_job(runtime, failed["id"], request)["id"] == new["id"]
    with pytest.raises(ValueError, match="different"):
        retry_job(runtime, failed["id"], RetryRequest(idempotency_key="original"))
    with pytest.raises(ValueError, match="different"):
        enqueue(
            runtime,
            JobRequest(
                kind="evaluation", dataset="challenge", profile="demo-fast", idempotency_key="retry"
            ),
        )


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_retry_rejects_jobs_that_have_not_failed(runtime, status):
    queued = enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="status"))
    with runtime.engine.begin() as db:
        db.execute(
            text("UPDATE jobs SET status=:status WHERE id=:id"),
            {"status": status, "id": queued["id"]},
        )
    with pytest.raises(ValueError, match="terminal failed"):
        retry_job(runtime, queued["id"], RetryRequest(idempotency_key="new"))


def test_legacy_retry_requires_recorded_pins(runtime):
    failed = failed_job(runtime)
    payload = json.loads(failed["payload"])
    del payload["dataset_sha256"]
    with runtime.engine.begin() as db:
        db.execute(
            text("UPDATE jobs SET payload=:payload WHERE id=:id"),
            {"payload": json.dumps(payload), "id": failed["id"]},
        )
    with pytest.raises(ValueError, match="Legacy"):
        retry_job(runtime, failed["id"], RetryRequest(idempotency_key="legacy"))


@pytest.mark.parametrize("payload", ["private-provider-content", "[]", '{"kind":"unknown"}'])
def test_corrupted_retry_inputs_fail_with_a_bounded_message(runtime, payload):
    failed = failed_job(runtime)
    with runtime.engine.begin() as db:
        db.execute(
            text("UPDATE jobs SET payload=:payload WHERE id=:id"),
            {"payload": payload, "id": failed["id"]},
        )
    with pytest.raises(ValueError, match="^Job inputs are invalid; submit a new job$"):
        retry_job(runtime, failed["id"], RetryRequest(idempotency_key="invalid"))


def test_retry_api_and_cli_share_idempotency(runtime, monkeypatch):
    failed = failed_job(runtime)
    monkeypatch.setenv("SORTER_DATA_DIR", str(runtime.settings.data_dir))
    settings = runtime.settings.model_copy(update={"worker_enabled": False})
    with TestClient(create_app(settings)) as client:
        result = client.post(
            f"/api/v1/jobs/{failed['id']}/retry", json={"idempotency_key": "api-retry"}
        )
        assert result.status_code == 202
        new = result.json()
        assert (
            client.post(
                f"/api/v1/jobs/{new['id']}/retry", json={"idempotency_key": "bad"}
            ).status_code
            == 409
        )
        assert (
            client.post("/api/v1/jobs/missing/retry", json={"idempotency_key": "bad"}).status_code
            == 404
        )
    command = CliRunner().invoke(app, ["jobs", "retry", failed["id"], "api-retry"])
    assert command.exit_code == 0, command.output
    assert json.loads(command.stdout)["id"] == new["id"]
    assert CliRunner().invoke(app, ["jobs", "retry", "missing", "bad"]).exit_code == 2
