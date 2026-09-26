import json
import re
from time import monotonic
from typing import Any

import httpx
from mcp.types import InitializeResult, ListPromptsResult, ListResourcesResult, ListToolsResult
from pydantic import BaseModel, Field

from mcp_sorter.network import NetworkDenied, authorize_url, client
from mcp_sorter.settings import Settings

PROTOCOL = "2025-11-25"
SUPPORTED_PROTOCOLS = {PROTOCOL, "2025-06-18", "2025-03-26"}


class ProbeResult(BaseModel):
    endpoint: str
    protocol: str
    tools: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    truncated: bool = False
    trust: str = "Unverified remote declarations; no tools were executed"


def probe(
    settings: Settings, endpoint: str, transport: httpx.BaseTransport | None = None
) -> ProbeResult:
    authorize_url(endpoint, settings)
    if endpoint not in settings.approved_probe_endpoints:
        raise NetworkDenied("The exact endpoint must be approved by the operator")
    deadline = monotonic() + settings.request_timeout
    headers = {"Accept": "application/json, text/event-stream"}
    sequence = 0

    with client(settings, transport) as http:

        def exchange(method: str, params: dict[str, Any], notify: bool = False) -> dict[str, Any]:
            nonlocal sequence
            sequence += 1
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError("Probe deadline exceeded")
            message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notify:
                message["id"] = sequence
            with http.stream(
                "POST", endpoint, headers=headers, json=message, timeout=remaining
            ) as response:
                response.raise_for_status()
                if not notify and "application/json" not in response.headers.get(
                    "content-type", ""
                ):
                    raise ValueError("Probe requires JSON responses; streaming SSE is unsupported")
                session = response.headers.get("mcp-session-id")
                if session:
                    if not re.fullmatch(r"[\x21-\x7e]{1,256}", session):
                        raise ValueError("Invalid MCP session identifier")
                    headers["Mcp-Session-Id"] = session
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_048_576 or monotonic() > deadline:
                        raise ValueError("Probe response exceeds its bounds")
                if notify:
                    return {}
                # This deliberately bounded adapter accepts the protocol's JSON response form.
                # Persistent SSE streams are rejected instead of opening a background listener.
                payload = json.loads(raw)
                if (
                    not isinstance(payload, dict)
                    or payload.get("jsonrpc") != "2.0"
                    or payload.get("id") != sequence
                    or not isinstance(payload.get("result"), dict)
                ):
                    raise ValueError("Invalid MCP response envelope")
                return dict(payload["result"])

        hello = InitializeResult.model_validate(
            exchange(
                "initialize",
                {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-sorter-probe", "version": "0.1.0"},
                },
            )
        )
        if hello.protocolVersion not in SUPPORTED_PROTOCOLS:
            raise ValueError("Unsupported negotiated MCP protocol version")
        headers["MCP-Protocol-Version"] = hello.protocolVersion
        exchange("notifications/initialized", {}, notify=True)
        result = ProbeResult(endpoint=endpoint, protocol=hello.protocolVersion)
        for name, schema in (
            ("tools", ListToolsResult),
            ("resources", ListResourcesResult),
            ("prompts", ListPromptsResult),
        ):
            if getattr(hello.capabilities, name) is None:
                continue
            page = schema.model_validate(exchange(f"{name}/list", {}))
            values = getattr(page, name)
            if len(values) > 100:
                raise ValueError("Too many declared capabilities")
            names = [item.name for item in values]
            if any(len(name) > 256 for name in names):
                raise ValueError("Capability name exceeds the limit")
            setattr(result, name, names)
            result.truncated |= bool(page.nextCursor)
        return result
