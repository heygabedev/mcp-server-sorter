import json
import logging
from time import perf_counter
from uuid import uuid4

from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import CollectorRegistry, Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class Telemetry:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "sorter_requests", "HTTP requests", ["status"], registry=self.registry
        )
        self.duration = Histogram(
            "sorter_request_seconds", "HTTP request duration", registry=self.registry
        )
        self.provider = TracerProvider()
        self.tracer = self.provider.get_tracer("mcp_sorter")


class RequestTelemetry(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            allowed_origins = {
                str(request.base_url).rstrip("/"),
                *request.app.state.settings.trusted_origins,
            }
            if (origin and origin not in allowed_origins) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return JSONResponse(
                    {"detail": "Cross-origin writes are not allowed"}, status_code=403
                )
        telemetry: Telemetry = request.app.state.telemetry
        started = perf_counter()
        request_id = uuid4().hex
        with telemetry.tracer.start_as_current_span("http.request") as span:
            response = await call_next(request)
            span.set_attribute("http.response.status_code", response.status_code)
            telemetry.requests.labels(status=str(response.status_code)).inc()
            telemetry.duration.observe(perf_counter() - started)
            logging.getLogger("mcp_sorter.requests").info(
                json.dumps({"request_id": request_id, "status": response.status_code})
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
            return response
