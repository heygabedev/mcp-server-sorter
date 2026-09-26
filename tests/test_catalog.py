import json
import sqlite3

import httpx
import pytest

from mcp_sorter.catalog import Catalog
from mcp_sorter.settings import Settings
from mcp_sorter.sources import fetch_registry, fetch_repository
from mcp_sorter.storage import get_value, open_state, set_value


@pytest.fixture
def catalog(tmp_path):
    engine = open_state(tmp_path)
    result = Catalog(tmp_path, engine)
    result.seed_demo()
    yield result
    engine.dispose()


def test_snapshot_is_immutable_and_deduplicated(catalog):
    first = catalog.active()
    records = catalog.records()
    assert len(records) == 30
    assert all(r.simulated for r in records)
    assert catalog.seed_demo() == first
    second = catalog.publish(records[:-1], "2026-09-02T00:00:00Z")
    assert first != second
    assert catalog.active() == first
    catalog.activate(second)
    assert len(catalog.records()) == 29
    assert len(catalog.records(first)) == 30
    assert set(catalog.snapshots()) == {first, second}


def test_search_statistics_are_isolated_between_snapshots(catalog):
    first = catalog.active()
    with catalog.connect(first) as db:
        before = db.execute(
            "SELECT id, bm25(search) FROM search WHERE search MATCH 'github'"
        ).fetchall()
    records = catalog.records()
    catalog.publish(records[:2], "2026-10-01T00:00:00Z")
    with catalog.connect(first) as db:
        after = db.execute(
            "SELECT id, bm25(search) FROM search WHERE search MATCH 'github'"
        ).fetchall()
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM servers")
    assert before == after


def test_invalid_refresh_keeps_current_catalog(catalog):
    original = catalog.active()
    for records in ([], catalog.records() * 2):
        with pytest.raises(ValueError):
            catalog.publish(records, "2026-09-01")
    with pytest.raises(ValueError):
        catalog.activate("../../state")
    with pytest.raises(ValueError, match="Unknown"):
        catalog.activate("0" * 64)
    assert catalog.active() == original


def test_manifest_and_records_are_checked(catalog):
    snapshot = catalog.active()
    db = sqlite3.connect(catalog.path(snapshot))
    db.execute("UPDATE servers SET payload = '{}' WHERE id = 'demo/github'")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="records"):
        catalog.validate(snapshot)


def test_missing_active_and_incompatible_state(tmp_path):
    engine = open_state(tmp_path)
    with pytest.raises(ValueError, match="No active"):
        Catalog(tmp_path, engine).active()
    assert get_value(engine, "missing") is None
    set_value(engine, "schema_version", "999")
    engine.dispose()
    with pytest.raises(ValueError, match="Unsupported"):
        open_state(tmp_path)


def test_registry_pagination_and_quarantine():
    entry = {"server": {"name": "org/server", "version": "1", "description": "A server"}}

    def respond(request):
        if request.url.params.get("cursor"):
            return httpx.Response(200, json={"servers": [], "metadata": {}})
        return httpx.Response(
            200, json={"servers": [entry, {}], "metadata": {"nextCursor": "next"}}
        )

    settings = Settings(mode="live", allowed_hosts=("registry.modelcontextprotocol.io",))
    records, rejected = fetch_registry(settings, httpx.MockTransport(respond))
    assert records[0].auth == "unknown"
    assert records[0].license is None
    assert len(rejected) == 1


@pytest.mark.parametrize("payload", [{}, {"servers": [], "metadata": {"nextCursor": "again"}}])
def test_registry_rejects_broken_pagination(payload):
    settings = Settings(mode="live", allowed_hosts=("registry.modelcontextprotocol.io",))
    with pytest.raises(ValueError):
        fetch_registry(settings, httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))


def test_repository_contract_and_invalid_input():
    settings = Settings(mode="live", allowed_hosts=("api.github.com",))
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"id": 123, "archived": True})
    )
    assert fetch_repository("example", "repo", settings, transport)["archived"] is True
    with pytest.raises(ValueError):
        fetch_repository("../escape", "repo", settings, transport)
    with pytest.raises(ValueError, match="Unexpected"):
        fetch_repository(
            "example", "repo", settings, httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )


def test_upstream_size_limit():
    settings = Settings(mode="live", allowed_hosts=("registry.modelcontextprotocol.io",))
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=json.dumps("a" * 2_000_001))
    )
    with pytest.raises(ValueError, match="megabytes"):
        fetch_registry(settings, transport)
