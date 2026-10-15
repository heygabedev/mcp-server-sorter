import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from mcp_sorter.api import create_app
from mcp_sorter.jobs import JobRequest, enqueue, get_job, run_once
from mcp_sorter.telemetry import Telemetry, job_links, request_trace


def test_request_and_job_spans_link_without_sensitive_content_or_metric_labels(runtime, caplog):
    application = create_app(runtime.settings.model_copy(update={"worker_enabled": False}))
    with caplog.at_level("INFO", logger="mcp_sorter.requests"), TestClient(application) as client:
        job = client.post(
            "/api/v1/jobs?query=private-marker",
            json={"kind": "catalog-refresh", "idempotency_key": "private-marker"},
            headers={"authorization": "private-marker"},
        ).json()
        telemetry = application.state.telemetry
        assert run_once(runtime, telemetry)
        attempts = get_job(runtime.engine, job["id"])["attempt_history"]
        client.get(f"/api/v1/jobs/{job['id']}?secret=private-marker")
        client.get("/private-marker")
        spans = telemetry.spans.snapshot()
        executed = next(span for span in spans if span["name"] == "job.execute")
        assert executed["links"] == [
            {"trace_id": job["request_trace_id"], "span_id": job["request_span_id"]}
        ]
        assert executed["trace_id"] == attempts[0]["trace_id"]
        assert executed["attributes"]["job.id"] == job["id"]
        assert executed["attributes"]["job.outcome"] == "completed"
        request_spans = [span for span in spans if span["name"] == "http.request"]
        assert {span["attributes"]["http.route"] for span in request_spans} >= {
            "/api/v1/jobs",
            "/api/v1/jobs/{identifier}",
            "unmatched",
        }
        metrics = client.get("/metrics").text
        assert "sorter_job_seconds" in metrics and job["id"] not in metrics
        assert "private-marker" not in json.dumps(spans) + metrics + caplog.text


def test_retry_spans_link_to_retry_request_and_record_safe_failure_outcomes(runtime, monkeypatch):
    application = create_app(runtime.settings.model_copy(update={"worker_enabled": False}))
    with TestClient(application) as client:
        job = client.post(
            "/api/v1/jobs", json={"kind": "catalog-refresh", "idempotency_key": "trace-failure"}
        ).json()
        telemetry = application.state.telemetry

        def timeout(*args):
            raise TimeoutError("private-provider-content")

        with monkeypatch.context() as patch:
            patch.setattr("mcp_sorter.jobs.execute", timeout)
            for _ in range(3):
                with runtime.engine.begin() as db:
                    db.execute(
                        text("UPDATE jobs SET available_at=0 WHERE id=:id"), {"id": job["id"]}
                    )
                assert run_once(runtime, telemetry)
        retry = client.post(
            f"/api/v1/jobs/{job['id']}/retry", json={"idempotency_key": "trace-retry"}
        ).json()
        assert retry["request_trace_id"] != job["request_trace_id"]
        assert run_once(runtime, telemetry)
        spans = [s for s in telemetry.spans.snapshot() if s["name"] == "job.execute"]
        assert [s["attributes"]["job.outcome"] for s in spans] == ["failed"] * 3 + ["completed"]
        assert all(s["attributes"]["job.error_code"] == "timeout" for s in spans[:3])
        assert spans[-1]["links"][0]["trace_id"] == retry["request_trace_id"]
        assert "private-provider-content" not in json.dumps(telemetry.spans.snapshot())


def test_invalid_links_and_unapproved_attributes_are_not_exported():
    assert request_trace() == (None, None)
    for trace, span in [(None, None), ("bad", "bad"), ("0" * 32, "0" * 16)]:
        assert job_links(trace, span) == []
    telemetry = Telemetry()
    try:
        with telemetry.tracer.start_as_current_span(
            "example", attributes={"unsafe": "private-marker"}
        ):
            assert request_trace()[0] is not None
        assert "private-marker" not in json.dumps(telemetry.spans.snapshot())
    finally:
        telemetry.close()


@pytest.mark.parametrize("corrupted", ["not json", "[]"])
def test_corrupted_job_payload_does_not_crash_tracing_or_store_raw_content(runtime, corrupted):
    job = enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="corrupted"))
    with runtime.engine.begin() as db:
        db.execute(
            text("UPDATE jobs SET payload=:payload WHERE id=:id"),
            {"id": job["id"], "payload": corrupted},
        )
    telemetry = Telemetry()
    try:
        assert run_once(runtime, telemetry)
        span = telemetry.spans.snapshot()[-1]
        assert span["attributes"]["job.kind"] == "unknown"
        assert span["attributes"]["job.outcome"] == "failed"
    finally:
        telemetry.close()


def test_lost_lease_is_reported_without_overwriting_job_state(runtime, monkeypatch):
    enqueue(runtime, JobRequest(kind="catalog-refresh", idempotency_key="lost"))
    monkeypatch.setattr("mcp_sorter.jobs.finish", lambda *args, **kwargs: False)
    telemetry = Telemetry()
    try:
        assert run_once(runtime, telemetry)
        assert telemetry.spans.snapshot()[-1]["attributes"]["job.outcome"] == "lease-lost"
    finally:
        telemetry.close()
