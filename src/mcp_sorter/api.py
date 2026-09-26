from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from mcp_sorter import __version__
from mcp_sorter.settings import Settings
from mcp_sorter.telemetry import RequestTelemetry, Telemetry


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    application = FastAPI(
        title="MCP Server Sorter", version=__version__, docs_url=None, redoc_url=None
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

    return application
