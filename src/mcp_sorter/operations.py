from collections.abc import Callable
from time import time

from fastapi import APIRouter, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.responses import Response

from mcp_sorter.evaluation import EvaluationReport, compare_reports, report_path
from mcp_sorter.events import recent_events
from mcp_sorter.jobs import JobRequest, enqueue, get_job, list_jobs
from mcp_sorter.runtime import Runtime
from mcp_sorter.telemetry import Telemetry


def status(runtime: Runtime) -> dict[str, object]:
    jobs = list_jobs(runtime.engine)
    events = recent_events(runtime.engine)
    alerts = []
    if any(job["status"] == "failed" for job in jobs):
        alerts.append("A job exhausted its retry budget")
    if any(job["status"] == "running" and job["lease_until"] <= time() for job in jobs):
        alerts.append("A worker lease expired; restart a worker to recover the job")
    if any(event["kind"] == "fallback" for event in events):
        alerts.append("Recent model requests used deterministic fallback")
    return {
        "mode": runtime.settings.mode,
        "jobs": jobs,
        "events": events,
        "alerts": alerts,
        "worker_enabled": runtime.settings.worker_enabled,
        "telemetry": "Local memory and SQLite only; no remote exporter configured",
    }


def router(get_runtime: Callable[[], Runtime], telemetry: Telemetry) -> APIRouter:
    routes = APIRouter()

    @routes.post("/api/v1/jobs", status_code=202)
    def submit(request: JobRequest) -> dict[str, object]:
        try:
            return enqueue(get_runtime(), request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @routes.get("/api/v1/jobs/{identifier}")
    def job(identifier: str) -> dict[str, object]:
        try:
            return get_job(get_runtime().engine, identifier)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @routes.get("/api/v1/operations")
    def operations() -> dict[str, object]:
        return {**status(get_runtime()), "traces": telemetry.spans.snapshot()}

    @routes.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        with get_runtime().engine.connect() as db:
            counts = {
                str(row[0]): int(row[1])
                for row in db.execute(text("SELECT status,COUNT(*) FROM jobs GROUP BY status"))
            }
            for label in ("queued", "running", "completed", "failed"):
                telemetry.jobs.labels(status=label).set(counts.get(label, 0))
            telemetry.fallbacks.set(
                db.execute(text("SELECT COUNT(*) FROM events WHERE kind='fallback'")).scalar_one()
            )
        return Response(
            generate_latest(telemetry.registry), headers={"Content-Type": CONTENT_TYPE_LATEST}
        )

    @routes.get("/api/v1/evaluation-comparison")
    def compare(baseline: str, candidate: str) -> dict[str, object]:
        try:
            reports = [
                EvaluationReport.model_validate_json(
                    report_path(get_runtime().settings.data_dir, identifier).read_text("utf-8")
                )
                for identifier in (baseline, candidate)
            ]
            return compare_reports(*reports)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return routes
