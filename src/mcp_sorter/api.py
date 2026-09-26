from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from mcp_sorter import __version__
from mcp_sorter.models import ServerRecord
from mcp_sorter.ranking import Ranking, RankRequest, compare, rank
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings
from mcp_sorter.telemetry import RequestTelemetry, Telemetry


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = Runtime(config)
        try:
            yield
        finally:
            app.state.runtime.close()
            app.state.telemetry.provider.shutdown()

    application = FastAPI(
        title="MCP Server Sorter",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = config
    application.state.telemetry = Telemetry()
    application.add_middleware(RequestTelemetry)
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
            return rank(application.state.runtime.catalog, request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.post("/api/v1/comparisons", response_model=list[ServerRecord])
    def comparisons(request: CompareRequest) -> list[ServerRecord]:
        try:
            return compare(application.state.runtime.catalog, request.ids, request.snapshot)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return application


class CompareRequest(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=4)
    snapshot: str | None = None
