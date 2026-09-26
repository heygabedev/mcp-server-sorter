import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.cli import app
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import (
    catalog_diff,
    export_selection,
    get_selection,
    import_selection,
    list_selections,
    save_selection,
)
from mcp_sorter.settings import Settings


def test_selection_pins_survive_activation_and_export(runtime, tmp_path):
    saved = save_selection(runtime, "Developer tools", ["demo/github", "demo/slack"])
    old = runtime.catalog.active()
    records = runtime.catalog.records()
    new = runtime.catalog.publish(records[:1], "2026-09-02T00:00:00Z")
    runtime.catalog.activate(new)
    assert get_selection(runtime, saved.id).snapshot == old
    bundle = export_selection(runtime, saved.id)
    other = Runtime(Settings(data_dir=tmp_path / "other"))
    try:
        imported = import_selection(other, bundle)
        assert imported.id != saved.id
        assert imported.servers == saved.servers
        assert len(list_selections(other)) == 1
    finally:
        other.close()
    assert len(catalog_diff(runtime, old, new)["removed"]) == 29


def test_selection_validation_and_checksum(runtime):
    for ids in ([], ["missing"], ["demo/github"] * 2):
        with pytest.raises(ValueError):
            save_selection(runtime, "Example", ids)
    with pytest.raises(ValueError):
        get_selection(runtime, "missing")
    saved = save_selection(runtime, "Example", ["demo/github"])
    bundle = export_selection(runtime, saved.id)
    bundle.selection.name = "Changed after export"
    with pytest.raises(ValueError, match="checksum"):
        import_selection(runtime, bundle)


def test_diff_detects_modified_and_deprecated_records(runtime):
    old = runtime.catalog.active()
    records = runtime.catalog.records()
    records[0] = records[0].model_copy(update={"status": "deprecated", "version": "2.0-demo"})
    new = runtime.catalog.publish(records, "2026-09-02T00:00:00Z")
    diff = catalog_diff(runtime, old, new)
    assert diff["changed"] == [records[0].id]
    assert diff["deprecated"] == [records[0].id]


def test_collection_api_and_cross_origin_protection(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as http:
        body = {"name": "My tools", "ids": ["demo/github"]}
        assert (
            http.post(
                "/api/v1/collections", json=body, headers={"origin": "https://attacker.test"}
            ).status_code
            == 403
        )
        assert (
            http.post(
                "/api/v1/collections", json=body, headers={"sec-fetch-site": "cross-site"}
            ).status_code
            == 403
        )
        response = http.post("/api/v1/collections", json=body)
        assert response.status_code == 201
        identifier = response.json()["id"]
        assert len(http.get("/api/v1/collections").json()) == 1
        bundle = http.get(f"/api/v1/collections/{identifier}/export").json()
        assert http.post("/api/v1/collections/import", json=bundle).status_code == 201
        bundle["content_sha256"] = "0" * 64
        assert http.post("/api/v1/collections/import", json=bundle).status_code == 422
        assert http.get("/api/v1/collections/missing/export").status_code == 404
        assert (
            http.post(
                "/api/v1/collections", json={"name": "Missing", "ids": ["missing"]}
            ).status_code
            == 422
        )
        active = http.get("/api/v1/versions").json()["active_catalog"]
        assert (
            http.get("/api/v1/versions/diff", params={"before": active, "after": active}).json()[
                "changed"
            ]
            == []
        )
        assert (
            http.get(
                "/api/v1/versions/diff", params={"before": "invalid", "after": active}
            ).status_code
            == 422
        )


def test_version_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTER_DATA_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["versions"])
    assert result.exit_code == 0
    snapshot = json.loads(result.stdout)["active_catalog"]
    result = runner.invoke(app, ["catalog", "diff", snapshot, snapshot])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["changed"] == []
