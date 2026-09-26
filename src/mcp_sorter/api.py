from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from mcp_sorter import __version__
from mcp_sorter.evaluation import EvaluationReport, evaluate, report_path, save_report
from mcp_sorter.gateways import rank_with_profile
from mcp_sorter.http_limits import BodyLimit
from mcp_sorter.jobs import Worker
from mcp_sorter.models import ServerRecord
from mcp_sorter.operations import router as operations_router
from mcp_sorter.ranking import Profile, Ranking, RankRequest, compare, rank
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


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = Runtime(config)
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
    )
    application.state.settings = config
    application.state.telemetry = Telemetry()
    application.include_router(
        operations_router(lambda: application.state.runtime, application.state.telemetry)
    )
    application.add_middleware(RequestTelemetry)
    application.add_middleware(BodyLimit)
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

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
        catalog = application.state.runtime.catalog
        return {"active_catalog": catalog.active(), "snapshots": catalog.snapshots()}

    @application.get("/api/v1/versions/diff")
    def version_diff(before: str, after: str) -> dict[str, list[str]]:
        try:
            return catalog_diff(application.state.runtime, before, after)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

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
