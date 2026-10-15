import json
import sqlite3
from contextlib import closing
from threading import Event
from time import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from mcp_sorter.api import create_app
from mcp_sorter.job_diagnostics import failure_code
from mcp_sorter.jobs import JobRequest, claim, enqueue, finish, get_job, run_once
from mcp_sorter.recovery import backup, restore, validate_backup
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings


@pytest.mark.parametrize(
    "error,expected",
    [
        (TimeoutError("secret"), "timeout"),
        (httpx.ReadTimeout("secret"), "timeout"),
        (httpx.ConnectError("secret"), "provider-unavailable"),
        (ConnectionError("secret"), "provider-unavailable"),
        (FileNotFoundError("secret"), "artifact-unavailable"),
        (PermissionError("secret"), "state-store-unavailable"),
        (OperationalError("secret", {}, None), "state-store-unavailable"),
        (ValueError("secret"), "invalid-data"),
        (RuntimeError("secret"), "execution-failed"),
    ],
)
def test_failure_categories_never_use_exception_text(error, expected):
    assert failure_code(error) == expected


@pytest.mark.parametrize("status,expected", [(429, "rate-limited"), (503, "provider-unavailable")])
def test_http_failure_category_uses_only_status(status, expected):
    error = httpx.HTTPStatusError(
        "secret",
        request=httpx.Request("GET", "https://example.test/secret"),
        response=httpx.Response(status, text="secret"),
    )
    assert failure_code(error) == expected


def test_attempt_history_survives_lease_fencing_and_completion(runtime):
    queued = enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="history"))
    now = time()
    first = claim(runtime.engine, now)
    second = claim(runtime.engine, now + 31)
    assert not finish(runtime, first, {}, now=now + 32)
    assert finish(runtime, second, {}, now=now + 32)
    attempts = get_job(runtime.engine, queued["id"])["attempt_history"]
    assert [a["outcome"] for a in attempts] == ["lease-expired", "completed"]
    assert attempts[0]["started_at"] == now and attempts[0]["finished_at"] == now + 31
    assert attempts[1]["finished_at"] == now + 32 and attempts[1]["error_code"] is None


def test_failed_attempts_redact_result_and_unknown_error_codes(runtime):
    queued = enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="failure"))
    current = claim(runtime.engine)
    assert finish(
        runtime, current, {"secret": "provider content"}, failed=True, error_code="secret"
    )
    stored = get_job(runtime.engine, queued["id"])
    assert stored["status"] == "queued" and stored["error_code"] == "execution-failed"
    assert stored["result"] is None and "secret" not in json.dumps(stored)
    assert stored["attempt_history"][0]["outcome"] == "failed"
    with runtime.engine.begin() as db:
        db.execute(
            text("UPDATE jobs SET attempts=3,available_at=0 WHERE id=:id"), {"id": queued["id"]}
        )
    assert claim(runtime.engine) is None
    assert get_job(runtime.engine, queued["id"])["error_code"] == "retry-budget-exhausted"


def test_diagnostics_migration_preserves_legacy_jobs_without_inventing_attempts(runtime):
    queued = enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="legacy"))
    claim(runtime.engine)
    runtime.close()
    with closing(sqlite3.connect(runtime.settings.data_dir / "state.sqlite")) as db, db:
        db.execute("DROP TABLE job_attempts")
        db.execute("ALTER TABLE jobs DROP COLUMN parent_job_id")
        db.execute("ALTER TABLE jobs DROP COLUMN request_trace_id")
        db.execute("ALTER TABLE jobs DROP COLUMN request_span_id")
        db.execute("UPDATE alembic_version SET version_num='0006'")
        db.execute("UPDATE jobs SET lease_until=0")
    migrated = Runtime(runtime.settings)
    try:
        legacy = get_job(migrated.engine, queued["id"])
        assert legacy["payload"] == queued["payload"] and legacy["attempt_history"] == []
        backups = list((runtime.settings.data_dir / "backups").iterdir())
        assert len(backups) == 1 and validate_backup(backups[0]).state_revision == "0006"
        assert run_once(migrated)
        attempts = get_job(migrated.engine, queued["id"])["attempt_history"]
        assert len(attempts) == 1 and attempts[0]["attempt"] == 2
    finally:
        migrated.close()


def test_backup_recovery_closes_interrupted_attempts(runtime, tmp_path):
    queued = enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="restore"))
    claim(runtime.engine)
    source = backup(runtime)
    destination = tmp_path.parent / (tmp_path.name + "-diagnostics")
    restore(runtime, source, destination)
    restored = Runtime(Settings(data_dir=destination))
    try:
        interrupted = get_job(restored.engine, queued["id"])
        assert interrupted["status"] == "queued"
        assert interrupted["attempt_history"][0]["outcome"] == "interrupted"
        assert run_once(restored)
        assert [
            a["outcome"] for a in get_job(restored.engine, queued["id"])["attempt_history"]
        ] == ["interrupted", "completed"]
    finally:
        restored.close()


def test_attempt_diagnostics_are_exposed_through_the_job_api(runtime, monkeypatch):
    settings = runtime.settings.model_copy(update={"worker_enabled": False})
    with TestClient(create_app(settings)) as client:
        job = client.post(
            "/api/v1/jobs", json={"kind": "catalog-refresh", "idempotency_key": "api-diagnostics"}
        ).json()
        monkeypatch.setattr(
            "mcp_sorter.jobs.execute", lambda *args: (_ for _ in ()).throw(TimeoutError("secret"))
        )
        assert run_once(runtime)
        result = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert result["error_code"] == "timeout"
        assert result["attempt_history"][0]["error_code"] == "timeout"
        assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("fails", [False, True])
def test_heartbeat_loss_stops_safely_without_logging_database_details(
    runtime, monkeypatch, caplog, fails
):
    enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="heartbeat"))
    called = Event()

    def renew(*args):
        called.set()
        if fails:
            raise OperationalError("secret SQL", {"credential": "secret"}, None)
        return False

    def execute(*args):
        assert called.wait(2)
        return {}

    monkeypatch.setattr("mcp_sorter.jobs.HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr("mcp_sorter.jobs.renew", renew)
    monkeypatch.setattr("mcp_sorter.jobs.execute", execute)
    assert run_once(runtime)
    assert "secret" not in caplog.text
    if fails:
        assert "state-store-unavailable" in caplog.text
