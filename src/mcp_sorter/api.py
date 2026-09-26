from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_sorter import __version__
from mcp_sorter.evaluation import EvaluationReport, evaluate, report_path, save_report
from mcp_sorter.gateways import rank_with_profile
from mcp_sorter.http_limits import BodyLimit
from mcp_sorter.jobs import Worker
from mcp_sorter.models import ServerRecord
from mcp_sorter.operations import router as operations_router
from mcp_sorter.ranking import Profile, Ranking, RankRequest, compare, rank
from mcp_sorter.recovery import backup, service_lock, validate_backup
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import (
    Selection,
    SelectionExport,
    catalog_diff,
    export_selection,
    import_selection,
    list_selections,
    save_selection,
)
from mcp_sorter.settings import Settings
from mcp_sorter.telemetry import RequestTelemetry, Telemetry
from mcp_sorter.versioning import Configuration, activate, active_versions


class ErrorResponse(BaseModel):
    detail: str | list[dict[str, Any]]


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with service_lock(config.data_dir):
            app.state.runtime = Runtime(config)
            app.state.telemetry = Telemetry()
            worker = Worker(app.state.runtime)
            if config.worker_enabled:
                worker.start()
            try:
                yield
            finally:
                if config.worker_enabled:
                    worker.close()
                app.state.runtime.close()
                app.state.telemetry.close()

    application = FastAPI(
        title="MCP Server Sorter",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        responses={
            code: {"model": ErrorResponse} for code in (400, 403, 404, 405, 409, 413, 422, 503)
        },
    )
    application.state.settings = config
    application.include_router(
        operations_router(lambda: application.state.runtime, lambda: application.state.telemetry)
    )
    application.add_middleware(RequestTelemetry)
    application.add_middleware(BodyLimit)
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = dict(exc.headers or {})
        if exc.status_code == 405:
            methods = {
                method
                for route in application.routes
                if isinstance(route, Route) and route.path_regex.fullmatch(request.url.path)
                for method in route.methods or []
            }
            headers["Allow"] = ", ".join(sorted(methods))
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)

    @application.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "mode": config.mode}

    @application.get("/health/ready")
    def ready() -> dict[str, str]:
        runtime: Runtime = application.state.runtime
        try:
            snapshot = runtime.catalog.active()
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"status": "ok", "snapshot": snapshot}

    @application.get("/api/v1/servers", response_model=Ranking)
    def servers(q: str = Query(default="", max_length=500)) -> Ranking:
        return rank(application.state.runtime.catalog, RankRequest(query=q, limit=50))

    @application.post("/api/v1/rankings", response_model=Ranking)
    def rankings(request: RankRequest) -> Ranking:
        try:
            return rank_with_profile(application.state.runtime, request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.post("/api/v1/comparisons", response_model=list[ServerRecord])
    def comparisons(request: CompareRequest) -> list[ServerRecord]:
        try:
            return compare(application.state.runtime.catalog, request.ids, request.snapshot)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.post("/api/v1/evaluations", response_model=EvaluationReport)
    def evaluations(request: EvaluationRequest | None = None) -> EvaluationReport:
        request = request or EvaluationRequest()
        runtime: Runtime = application.state.runtime
        report = evaluate(
            runtime.catalog,
            profile=request.profile,
            ranker=lambda catalog, query: rank_with_profile(
                runtime, query.model_copy(update={"profile": request.profile})
            ),
        )
        save_report(config.data_dir, report)
        return report

    @application.get("/api/v1/evaluations", response_model=list[EvaluationReport])
    def evaluation_history() -> list[EvaluationReport]:
        return [
            EvaluationReport.model_validate_json(path.read_text("utf-8"))
            for path in sorted(
                (config.data_dir / "reports").glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:50]
        ]

    @application.get("/api/v1/evaluations/{identifier}", response_model=EvaluationReport)
    def evaluation_report(identifier: str) -> EvaluationReport:
        try:
            return EvaluationReport.model_validate_json(
                report_path(config.data_dir, identifier).read_text("utf-8")
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, "Evaluation report not found") from exc

    @application.get("/api/v1/collections", response_model=list[Selection])
    def collections() -> list[Selection]:
        return list_selections(application.state.runtime)

    @application.post("/api/v1/collections", response_model=Selection, status_code=201)
    def create_collection(request: CollectionRequest) -> Selection:
        try:
            return save_selection(
                application.state.runtime, request.name, request.ids, request.snapshot
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.get("/api/v1/collections/{identifier}/export", response_model=SelectionExport)
    def export_collection(identifier: str) -> SelectionExport:
        try:
            return export_selection(application.state.runtime, identifier)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @application.post("/api/v1/collections/import", response_model=Selection, status_code=201)
    def import_collection(bundle: SelectionExport) -> Selection:
        try:
            return import_selection(application.state.runtime, bundle)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.get("/api/v1/versions")
    def versions() -> dict[str, object]:
        runtime: Runtime = application.state.runtime
        snapshot, configuration = active_versions(runtime.engine)
        return {
            "active_catalog": snapshot,
            "active_configuration": configuration,
            "snapshots": runtime.catalog.snapshots(),
            "configurations": runtime.configurations.versions(),
        }

    @application.post("/api/v1/configurations", status_code=201)
    def configurations(request: Configuration) -> dict[str, str]:
        return {"id": application.state.runtime.configurations.publish(request)}

    @application.post("/api/v1/versions/activate")
    def activate_versions(request: ActivationRequest) -> dict[str, str]:
        runtime: Runtime = application.state.runtime
        try:
            activate(
                runtime.catalog, runtime.configurations, request.snapshot, request.configuration
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"snapshot": request.snapshot, "configuration": request.configuration}

    @application.post("/api/v1/backups", status_code=201)
    def create_backup() -> dict[str, object]:
        path = backup(application.state.runtime)
        return {"id": path.name, "manifest": validate_backup(path).model_dump()}

    @application.get("/api/v1/backups")
    def backups() -> list[str]:
        directory = config.data_dir / "backups"
        return sorted(
            path.parent.name
            for path in directory.glob("*/manifest.json")
            if not path.parent.name.startswith(".")
        )

    @application.get("/api/v1/backups/{identifier}")
    def verify_backup(identifier: str) -> dict[str, object]:
        import re

        try:
            if re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
                raise ValueError("Invalid backup identifier")
            return validate_backup(config.data_dir / "backups" / identifier).model_dump()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.get("/api/v1/versions/diff")
    def version_diff(before: str, after: str) -> dict[str, list[str]]:
        try:
            return catalog_diff(application.state.runtime, before, after)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    static = Path(__file__).parent / "static"
    if static.is_dir():
        application.mount("/", StaticFiles(directory=static, html=True), name="web")
    return application


class CompareRequest(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=4)
    snapshot: str | None = None


class EvaluationRequest(BaseModel):
    profile: Profile = "baseline"


class CollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    ids: list[str] = Field(min_length=1, max_length=50)
    snapshot: str | None = None


class ActivationRequest(BaseModel):
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration: str = Field(pattern=r"^[a-f0-9]{64}$")
