from unittest.mock import Mock

from fastapi.testclient import TestClient

from mcp_sorter.api import create_app
from mcp_sorter.evaluation import load_golden
from mcp_sorter.settings import Settings


def test_workspace_tracks_installed_version_and_active_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_sorter.api.__version__", "0.2.0rc1")
    app = create_app(Settings(data_dir=tmp_path, worker_enabled=False))
    with TestClient(app) as http:
        catalog = app.state.runtime.catalog
        original = catalog.active()
        records = catalog.records()
        first = http.get("/api/v1/workspace").json()
        assert first["application_version"] == "0.2.0rc1"
        assert first["mode"] == "demo"
        assert first["catalog"] == {
            "snapshot": original,
            "total": len(records),
            "simulated": len(records),
        }
        # Catalog totals include deprecated records and do not shrink with search results.
        assert len(http.get("/api/v1/servers?q=github").json()["results"]) == 1
        assert http.get("/api/v1/workspace").json()["catalog"] == first["catalog"]
        changed = catalog.publish(
            [records[0].model_copy(update={"simulated": False}), records[1]],
            "2026-09-26T00:00:00Z",
        )
        catalog.activate(changed)
        # Summary queries must not decode every full record to count a large catalog.
        monkeypatch.setattr(
            catalog, "records", Mock(side_effect=AssertionError("Unexpected full catalog scan"))
        )
        assert http.get("/api/v1/workspace").json()["catalog"] == {
            "snapshot": changed,
            "total": 2,
            "simulated": 1,
        }
        catalog.activate(original)
        assert http.get("/api/v1/workspace").json()["catalog"] == first["catalog"]


def test_live_workspace_with_no_catalog_still_reports_runtime_and_dataset(tmp_path):
    with TestClient(
        create_app(Settings(data_dir=tmp_path, mode="live", worker_enabled=False))
    ) as http:
        response = http.get("/api/v1/workspace")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["mode"] == "live"
    assert metadata["catalog"] is None
    dataset = load_golden()
    assert metadata["evaluation_dataset"] == {
        "version": dataset.version,
        "total": len(dataset.cases),
        "development": sum(case.split == "development" for case in dataset.cases),
        "heldout": sum(case.split == "heldout" for case in dataset.cases),
    }
