import json
import socket
import sys

import anyio
import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typer.testing import CliRunner

from mcp_sorter.cli import app
from mcp_sorter.mcp_server import create_mcp
from mcp_sorter.network import GuardedTransport, NetworkDenied, pin_public_destination
from mcp_sorter.probes import probe
from mcp_sorter.settings import Settings


def test_stdio_round_trip_and_read_only_tools(tmp_path):
    async def journey():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_sorter.cli", "mcp"],
            env={"SORTER_DATA_DIR": str(tmp_path), "SORTER_MODE": "demo"},
        )
        with anyio.fail_after(30):
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert {t.name for t in tools} == {"search_servers", "compare_servers"}
                assert all(t.annotations.readOnlyHint for t in tools)
                result = await session.call_tool("search_servers", {"query": "github"})
                assert not result.isError
                assert result.structuredContent["results"][0]["server"]["id"] == "demo/github"
                result = await session.call_tool(
                    "compare_servers", {"ids": ["demo/github", "demo/slack"]}
                )
                assert not result.isError
                assert (await session.call_tool("delete_catalog", {})).isError

    anyio.run(journey)


def test_mcp_service_and_cli_share_local_ranking(runtime, monkeypatch):
    server = create_mcp(runtime)

    async def call():
        result = await server.call_tool("search_servers", {"query": "github"})
        assert result[1]["results"][0]["server"]["id"] == "demo/github"
        result = await server.call_tool("compare_servers", {"ids": ["demo/github", "demo/slack"]})
        assert result[1]["result"][0]["id"] == "demo/github"

    anyio.run(call)
    monkeypatch.setattr("mcp_sorter.mcp_server.FastMCP.run", lambda *a, **kw: None)
    monkeypatch.setenv("SORTER_DATA_DIR", str(runtime.settings.data_dir))
    assert CliRunner().invoke(app, ["mcp"]).exit_code == 0
    assert CliRunner().invoke(app, ["probe", "https://probe.test/mcp"]).exit_code != 0


def settings():
    return Settings(
        mode="live",
        allowed_hosts=("probe.test",),
        approved_probe_endpoints=("https://probe.test/mcp",),
    )


def responder(seen, change=lambda method, payload: payload):
    def handle(request):
        body = json.loads(request.content)
        method = body["method"]
        seen.append(method)
        if method == "notifications/initialized":
            return httpx.Response(202)
        payload = {
            "initialize": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            },
            "tools/list": {
                "tools": [{"name": "read_file", "inputSchema": {"type": "object"}}],
                "nextCursor": "another-page",
            },
            "resources/list": {"resources": [{"name": "guide", "uri": "https://probe.test/guide"}]},
            "prompts/list": {"prompts": [{"name": "summarize"}]},
        }[method]
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "result": change(method, payload)},
            headers={"mcp-session-id": "session-1"},
        )

    return httpx.MockTransport(handle)


def test_probe_only_initializes_and_lists_capabilities(monkeypatch):
    seen = []
    result = probe(settings(), "https://probe.test/mcp", responder(seen))
    assert result.tools == ["read_file"] and result.resources == ["guide"]
    assert result.prompts == ["summarize"] and result.truncated
    assert seen == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "resources/list",
        "prompts/list",
    ]
    monkeypatch.setattr("mcp_sorter.probes.probe", lambda *a: result)
    assert CliRunner().invoke(app, ["probe", "https://probe.test/mcp"]).exit_code == 0


@pytest.mark.parametrize(
    "case",
    ["protocol", "many", "name", "missing", "envelope", "redirect", "oversized", "sse", "session"],
)
def test_rejects_untrusted_probe_responses(case):
    def modify(method, payload):
        if case == "protocol" and method == "initialize":
            payload["protocolVersion"] = "unknown"
        if case == "many" and method == "tools/list":
            payload["tools"] *= 101
        if case == "name" and method == "tools/list":
            payload["tools"][0]["name"] = "x" * 257
        if case == "missing":
            return {}
        return payload

    transport = responder([], modify)
    raw = {
        "envelope": httpx.Response(200, json={"jsonrpc": "2.0", "id": 999, "result": {}}),
        "redirect": httpx.Response(302, headers={"location": "https://private.test"}),
        "oversized": httpx.Response(
            200, content=b"x" * 1_048_577, headers={"content-type": "application/json"}
        ),
        "sse": httpx.Response(200, headers={"content-type": "text/event-stream"}),
        "session": httpx.Response(200, json={}, headers={"mcp-session-id": "x" * 257}),
    }
    if case in raw:
        transport = httpx.MockTransport(lambda request: raw[case])
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        probe(settings(), "https://probe.test/mcp", transport)


def test_probe_approval_and_deadline(monkeypatch):
    with pytest.raises(NetworkDenied):
        probe(Settings(), "https://probe.test/mcp")
    with pytest.raises(NetworkDenied):
        probe(settings(), "https://probe.test/other")
    times = iter([0, 100])
    monkeypatch.setattr("mcp_sorter.probes.monotonic", lambda: next(times))
    with pytest.raises(ValueError, match="deadline"):
        probe(settings(), "https://probe.test/mcp", responder([]))


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["8.8.8.8", "10.0.0.1"],
        [],
    ],
)
def test_dns_rebinding_and_private_addresses_are_blocked(monkeypatch, addresses):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses
        ],
    )
    with pytest.raises(NetworkDenied):
        pin_public_destination(httpx.Request("POST", "https://probe.test/mcp"))


def test_transport_pins_socket_destination_preserving_tls_name(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    seen = []
    monkeypatch.setattr(
        httpx,
        "HTTPTransport",
        lambda **kw: httpx.MockTransport(
            lambda request: seen.append(request) or httpx.Response(200)
        ),
    )
    with httpx.Client(transport=GuardedTransport(settings())) as client:
        client.get("https://probe.test/mcp")
    assert seen[0].url.host == "8.8.8.8"
    assert seen[0].headers["host"] == "probe.test"
    assert seen[0].extensions["sni_hostname"] == "probe.test"
