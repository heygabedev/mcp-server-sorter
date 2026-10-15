import json
import logging
import re
from collections import deque
from collections.abc import Sequence
from threading import Lock
from time import perf_counter
from uuid import uuid4

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Link, SpanContext, TraceFlags, get_current_span
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SAFE_ATTRIBUTES = frozenset(
    {
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "request.id",
        "job.id",
        "job.kind",
        "job.attempt",
        "job.outcome",
        "job.error_code",
    }
)


def request_trace() -> tuple[str | None, str | None]:
    context = get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


def job_links(trace_id: str | None, span_id: str | None) -> list[Link]:
    if (
        not trace_id
        or not span_id
        or not re.fullmatch(r"[a-f0-9]{32}", trace_id)
        or not re.fullmatch(r"[a-f0-9]{16}", span_id)
    ):
        return []
    context = SpanContext(
        trace_id=int(trace_id, 16),
        span_id=int(span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return [Link(context)] if context.is_valid else []


class LocalSpans(SpanExporter):
    def __init__(self) -> None:
        self.items: deque[dict[str, object]] = deque(maxlen=100)
        self.lock = Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self.lock:
            for span in spans:
                self.items.append(
                    {
                        "trace_id": format(span.context.trace_id, "032x") if span.context else "",
                        "span_id": format(span.context.span_id, "016x") if span.context else "",
                        "name": span.name,
                        "status": (span.attributes or {}).get("http.response.status_code", 0),
                        "attributes": {
                            key: value
                            for key, value in (span.attributes or {}).items()
                            if key in SAFE_ATTRIBUTES
                        },
                        "links": [
                            {
                                "trace_id": format(link.context.trace_id, "032x"),
                                "span_id": format(link.context.span_id, "016x"),
                            }
                            for link in span.links
                        ],
                    }
                )
        return SpanExportResult.SUCCESS

    def snapshot(self) -> list[dict[str, object]]:
        with self.lock:
            return list(self.items)


class Telemetry:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "sorter_requests",
            "HTTP requests",
            ["status", "method", "route"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "sorter_request_seconds", "HTTP request duration", registry=self.registry
        )
        self.jobs = Gauge(
            "sorter_jobs", "Persisted jobs by state", ["status"], registry=self.registry
        )
        self.fallbacks = Gauge(
            "sorter_recent_fallbacks",
            "Fallback events in the retained local event window",
            registry=self.registry,
        )
        self.job_duration = Histogram(
            "sorter_job_seconds",
            "Job attempt duration",
            ["kind", "outcome"],
            registry=self.registry,
        )
        self.provider = TracerProvider()
        self.spans = LocalSpans()
        self.provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.tracer = self.provider.get_tracer("mcp_sorter")
        self.reader = InMemoryMetricReader()
        self.meter_provider = MeterProvider(metric_readers=[self.reader])
        meter = self.meter_provider.get_meter("mcp_sorter")
        self.otel_requests = meter.create_counter("sorter.http.requests")
        self.otel_duration = meter.create_histogram("sorter.http.duration", unit="s")

    def close(self) -> None:
        self.provider.shutdown()
        self.meter_provider.shutdown()


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
        method = (
            request.method
            if request.method
            in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
            else "_OTHER"
        )
        with telemetry.tracer.start_as_current_span(
            "http.request",
            record_exception=False,
            set_status_on_exception=False,
            attributes={"http.request.method": method, "request.id": request_id},
        ) as span:
            status = 500
            try:
                response = await call_next(request)
                status = response.status_code
            finally:
                span.set_attribute("http.response.status_code", status)
                route = getattr(request.scope.get("route"), "path_format", "unmatched")
                span.set_attribute("http.route", route)
                telemetry.requests.labels(status=str(status), method=method, route=route).inc()
                telemetry.duration.observe(perf_counter() - started)
                telemetry.otel_requests.add(1, {"status": status, "method": method, "route": route})
                telemetry.otel_duration.record(perf_counter() - started)
                logging.getLogger("mcp_sorter.requests").info(
                    json.dumps(
                        {
                            "request_id": request_id,
                            "trace_id": format(span.get_span_context().trace_id, "032x"),
                            "status": status,
                            "method": method,
                            "route": route,
                        }
                    )
                )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
            return response
