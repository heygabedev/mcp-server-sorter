from fastapi.testclient import TestClient

from mcp_sorter.api import create_app
from mcp_sorter.settings import Settings


def test_bundled_interface_assets_and_api_share_the_loopback_app(tmp_path, monkeypatch):
    package = tmp_path / "package"
    static = package / "static"
    assets = static / "assets"
    assets.mkdir(parents=True)
    (static / "index.html").write_text('<!doctype html><title>Sorter</title><div id="root"></div>')
    (assets / "app.js").write_text('document.title = "MCP Server Sorter";')
    monkeypatch.setattr("mcp_sorter.api.__file__", str(package / "api.py"))
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as http:
        assert "Sorter" in http.get("/").text
        assert "javascript" in http.get("/assets/app.js").headers["content-type"]
        assert http.get("/health/ready").status_code == 200
        assert http.get("/api/v1/servers?q=github").json()["results"]
        assert http.get("/missing").status_code == 404
        assert "script-src 'self'" in http.get("/").headers["content-security-policy"]
