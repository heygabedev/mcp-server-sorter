import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.cli import app
from mcp_sorter.models import Filters
from mcp_sorter.ranking import RankRequest, rank
from mcp_sorter.search import CatalogSearch, cursor_key, decode_cursor, search_catalog


@pytest.mark.parametrize("query", ["", "repositories", "notpresent", "***"])
def test_pages_match_deterministic_retrieval(runtime, query):
    request = CatalogSearch(query=query, limit=3)
    result = search_catalog(runtime.catalog, request)
    collected = list(result.results)
    while result.next_cursor:
        result = search_catalog(
            runtime.catalog, request.model_copy(update={"cursor": result.next_cursor})
        )
        assert {r.server.id for r in collected}.isdisjoint(r.server.id for r in result.results)
        collected.extend(result.results)
    baseline = rank(runtime.catalog, RankRequest(query=query, limit=50))
    assert collected == baseline.results
    assert result.total == len(collected)


def test_pagination_preserves_all_ties_and_snapshot_across_activation(runtime):
    server = runtime.catalog.records()[0]
    records = [server.model_copy(update={"id": f"test/{i:03d}"}) for i in range(107)]
    snapshot = runtime.catalog.publish(records, "2026-01-01T00:00:00Z")
    runtime.catalog.activate(snapshot)
    request = CatalogSearch(limit=7)
    first = search_catalog(runtime.catalog, request)
    runtime.catalog.activate(runtime.catalog.publish([server], "2026-02-01T00:00:00Z"))
    ids = [s.server.id for s in first.results]
    page = first
    while page.next_cursor:
        page = search_catalog(
            runtime.catalog, request.model_copy(update={"cursor": page.next_cursor})
        )
        assert page.snapshot == snapshot and page.total == 107
        assert set(ids).isdisjoint(item.server.id for item in page.results)
        ids.extend(item.server.id for item in page.results)
    assert ids == [f"test/{i:03d}" for i in range(107)]


@pytest.mark.parametrize(
    "change",
    [
        {"query": "changed"},
        {"limit": 3},
        {"filters": Filters(auth="oauth")},
        {"snapshot": "a" * 64},
        {"configuration": "b" * 64},
    ],
)
def test_cursor_cannot_change_the_search_or_version_pins(runtime, change):
    request = CatalogSearch(limit=2)
    page = search_catalog(runtime.catalog, request)
    with pytest.raises(ValueError, match="Cursor belongs"):
        search_catalog(
            runtime.catalog, request.model_copy(update={"cursor": page.next_cursor, **change})
        )


def test_cursor_rejects_tampering_and_invalid_signed_payload(runtime):
    request = CatalogSearch(limit=2)
    first = search_catalog(runtime.catalog, request)
    with pytest.raises(ValueError, match="Invalid continuation"):
        search_catalog(
            runtime.catalog, request.model_copy(update={"cursor": first.next_cursor + "x"})
        )
    key = cursor_key(runtime.engine)
    for malformed in ["bad.é", "bad." + "0" * 64, "bad", "💥.bad"]:
        with pytest.raises(ValueError, match="Invalid continuation"):
            decode_cursor(malformed, key)
    encoded = "!not-base64!"
    signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="Invalid continuation"):
        decode_cursor(encoded + "." + signature, key)
    for payload in [b"invalid json", b'{"version":99}', b"null"]:
        encoded = base64.urlsafe_b64encode(payload).decode()
        signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
        with pytest.raises(ValueError, match="Invalid continuation"):
            decode_cursor(encoded + "." + signature, key)


def test_api_paging_filters_and_reranking_boundary(tmp_path):
    from mcp_sorter.settings import Settings

    with TestClient(create_app(Settings(data_dir=tmp_path, worker_enabled=False))) as client:
        request = {"filters": {"auth": "none"}, "limit": 2}
        first = client.post("/api/v1/catalog/search", json=request).json()
        assert first["total"] > 2
        second = client.post(
            "/api/v1/catalog/search", json={**request, "cursor": first["next_cursor"]}
        ).json()
        assert {r["server"]["id"] for r in first["results"]}.isdisjoint(
            r["server"]["id"] for r in second["results"]
        )
        assert all(r["server"]["auth"] == "none" for r in second["results"])
        assert (
            client.post("/api/v1/catalog/search", json={"profile": "demo-fast"}).status_code == 422
        )
        assert client.post("/api/v1/catalog/search", json={"cursor": "bad"}).status_code == 422
        assert client.post("/api/v1/catalog/search", json={"cursor": "bad.é"}).status_code == 422


def test_cursor_key_survives_runtime_restart_and_concurrent_creation(runtime):
    from concurrent.futures import ThreadPoolExecutor

    from mcp_sorter.runtime import Runtime

    with ThreadPoolExecutor(max_workers=4) as workers:
        keys = list(workers.map(lambda _: cursor_key(runtime.engine), range(8)))
    assert len(set(keys)) == 1
    request = CatalogSearch(limit=2)
    first = search_catalog(runtime.catalog, request)
    restarted = Runtime(runtime.settings)
    try:
        second = search_catalog(
            restarted.catalog, request.model_copy(update={"cursor": first.next_cursor})
        )
        assert first.snapshot == second.snapshot
        assert first.results != second.results
    finally:
        restarted.close()


def test_cursor_preserves_configuration_when_weights_change(runtime):
    from mcp_sorter.versioning import Configuration, activate

    request = CatalogSearch(query="code repositories", limit=1)
    first = search_catalog(runtime.catalog, request)
    expected = rank(runtime.catalog, RankRequest(query=request.query, limit=50))
    other = runtime.configurations.publish(Configuration(name_weight=1.0, tags_weight=10.0))
    activate(runtime.catalog, runtime.configurations, first.snapshot, other)
    collected = list(first.results)
    page = first
    while page.next_cursor:
        page = search_catalog(
            runtime.catalog, request.model_copy(update={"cursor": page.next_cursor})
        )
        assert page.configuration == first.configuration
        collected.extend(page.results)
    assert collected == expected.results


def test_cli_pages_match_api_and_reject_changed_cursor_options(tmp_path, monkeypatch):
    from mcp_sorter.settings import Settings

    monkeypatch.setenv("SORTER_DATA_DIR", str(tmp_path))
    runner = CliRunner()
    first = runner.invoke(app, ["search", "--limit", "2"])
    assert first.exit_code == 0, first.output
    page = json.loads(first.stdout)
    result = runner.invoke(app, ["search", "--limit", "2", "--cursor", page["next_cursor"]])
    assert result.exit_code == 0, result.output
    with TestClient(create_app(Settings(data_dir=tmp_path, worker_enabled=False))) as client:
        expected = client.post(
            "/api/v1/catalog/search", json={"limit": 2, "cursor": page["next_cursor"]}
        ).json()
    assert json.loads(result.stdout) == expected
    assert runner.invoke(app, ["search", "changed", "--cursor", page["next_cursor"]]).exit_code == 2
    assert runner.invoke(app, ["search", "--limit", "51"]).exit_code == 2
