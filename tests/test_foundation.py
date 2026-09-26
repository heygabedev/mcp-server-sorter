import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.cli import app
from mcp_sorter.network import NetworkDenied, authorize_url, client
from mcp_sorter.settings import Settings


def test_demo_defaults_ignore_presence_of_credentials(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder-not-a-key")
    settings = Settings()
    assert settings.mode == "demo"
    assert settings.spending_limit_usd == 0
    with client(settings) as http, pytest.raises(NetworkDenied, match="demo"):
        http.get("https://openrouter.ai/api/v1/models")


def test_allowed_request_and_no_redirect_following():
    settings = Settings(mode="live", allowed_hosts=("example.com",))
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(302, headers={"location": "https://elsewhere.test"})

    with client(settings, httpx.MockTransport(handler)) as http:
        assert http.get("https://example.com").status_code == 302
    assert len(seen) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/#x",
        "https://other.example",
        "https://example.com:8443",
    ],
)
def test_live_network_policy_rejects_unsafe_urls(url):
    with pytest.raises(NetworkDenied):
        authorize_url(url, Settings(mode="live", allowed_hosts=("example.com",)))


@pytest.mark.enable_socket
def test_health_and_host_validation(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as http:
        assert http.get("/health/live").json()["mode"] == "demo"
        assert http.get("/health/live", headers={"host": "attacker.test"}).status_code == 400


def test_cli_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_serve_binds_only_loopback(monkeypatch):
    calls = []
    monkeypatch.setattr("mcp_sorter.cli.uvicorn.run", lambda *args, **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(app, ["serve"])
    assert result.exit_code == 0
    assert calls[0]["host"] == "127.0.0.1"


@pytest.mark.enable_socket
def test_telemetry_does_not_log_query_or_credentials(tmp_path, caplog):
    with (
        caplog.at_level("INFO", logger="mcp_sorter.requests"),
        TestClient(create_app(Settings(data_dir=tmp_path))) as http,
    ):
        response = http.get("/health/live?secret=sensitive", headers={"Authorization": "secret"})
    records = [r.message for r in caplog.records if r.name == "mcp_sorter.requests"]
    assert records
    assert "sensitive" not in "".join(records)
    assert "Authorization" not in "".join(records)
    assert len(response.headers["X-Request-ID"]) == 32
