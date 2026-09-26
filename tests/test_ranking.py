import json
import socket
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pytest_socket import SocketConnectBlockedError
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.cli import app
from mcp_sorter.models import Filters
from mcp_sorter.ranking import RankRequest, compare, query_expression, rank
from mcp_sorter.settings import Settings


def test_relevance_evidence_and_stable_order(runtime):
    request = RankRequest(query="github pull requests")
    first = rank(runtime.catalog, request)
    assert first.results[0].server.id == "demo/github"
    assert first == rank(runtime.catalog, request)
    assert all(r.evidence_ids == [e.id for e in r.server.evidence] for r in first.results)
    assert all(
        r.server.status != "deprecated" for r in rank(runtime.catalog, RankRequest()).results
    )


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=35)
@given(
    st.sampled_from(["", "database", "github", "sqlite", "local", "messages"]),
    st.sampled_from(["local", "remote"]),
)
def test_stricter_filters_cannot_add_matches(runtime, query, deployment):
    all_results = rank(runtime.catalog, RankRequest(query=query, limit=50)).results
    restricted = rank(
        runtime.catalog, RankRequest(query=query, limit=50, filters=Filters(deployment=deployment))
    ).results
    assert {r.server.id for r in restricted} <= {r.server.id for r in all_results}
    assert all(r.server.deployment == deployment for r in restricted)


def test_filters_unknowns_deprecated_and_no_answer(runtime):
    results = rank(
        runtime.catalog,
        RankRequest(
            query="sqlite",
            filters=Filters(
                auth="none",
                transport="stdio",
                category="database",
                license="MIT",
                include_deprecated=True,
            ),
        ),
    ).results
    assert {r.server.id for r in results} == {"demo/sqlite", "demo/sqlite-legacy"}
    assert not rank(runtime.catalog, RankRequest(query="quantum teleportation")).results
    assert not rank(runtime.catalog, RankRequest(query='"*()')).results
    assert not rank(
        runtime.catalog, RankRequest(query="slack", filters=Filters(license="MIT"))
    ).results


@given(st.text(max_size=500))
def test_literal_query_compiler_cannot_emit_fts_control_syntax(query):
    expression = query_expression(query)
    for term in expression.split(" OR "):
        if term:
            assert term.startswith('"') and term.endswith('"')
            assert term.count('"') == 2
            assert all(char.isalnum() for char in term[1:-1])


def test_snapshot_pinning_and_comparison(runtime):
    old = runtime.catalog.active()
    second = runtime.catalog.publish(runtime.catalog.records()[:2], "2026-09-02T00:00:00Z")
    runtime.catalog.activate(second)
    assert rank(runtime.catalog, RankRequest(query="github", snapshot=old)).results
    assert [s.id for s in compare(runtime.catalog, ["demo/github", "demo/slack"], old)] == [
        "demo/github",
        "demo/slack",
    ]
    for ids in (["demo/github"], ["demo/github"] * 2, ["missing", "demo/github"]):
        with pytest.raises(ValueError):
            compare(runtime.catalog, ids, old)


def test_api_and_cli_match_without_external_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTER_DATA_DIR", str(tmp_path))
    with TestClient(create_app(Settings(data_dir=tmp_path))) as http:
        api_result = http.post("/api/v1/rankings", json={"query": "github"}).json()
        assert http.get("/health/ready").status_code == 200
        assert http.get("/api/v1/servers?q=github").json()["results"]
        assert (
            http.post(
                "/api/v1/comparisons", json={"ids": ["demo/github", "demo/slack"]}
            ).status_code
            == 200
        )
        assert (
            http.post("/api/v1/comparisons", json={"ids": ["missing", "demo/slack"]}).status_code
            == 422
        )
        assert http.post("/api/v1/rankings", json={"snapshot": "../../invalid"}).status_code == 422
    runner = CliRunner()
    output = runner.invoke(app, ["rank", "github"])
    assert output.exit_code == 0, output.output
    assert json.loads(output.stdout) == api_result
    assert runner.invoke(app, ["demo"]).exit_code == 0
    assert runner.invoke(app, ["compare", "demo/github", "demo/slack"]).exit_code == 0
    assert runner.invoke(app, ["compare", "demo/github", "missing"]).exit_code != 0
    assert runner.invoke(app, ["catalog", "sync"]).exit_code != 0
    with socket.socket() as sock, pytest.raises(SocketConnectBlockedError):
        sock.connect(("203.0.113.1", 443))


def test_unseeded_live_app_readiness(tmp_path):
    with TestClient(create_app(Settings(mode="live", data_dir=tmp_path))) as http:
        assert http.get("/health/ready").status_code == 503


def test_sql_order_preserves_evidence_freshness_and_id_ties(runtime):
    original = runtime.catalog.records()[0]
    records = [
        original.model_copy(
            update={
                "id": identifier,
                "status": "active",
                "name": "Equal",
                "description": "Equal",
                "updated_at": datetime.fromisoformat(updated),
                "evidence": evidence,
            }
        )
        for identifier, updated, evidence in [
            ("tie/b", "2026-09-03T00:00:00+00:00", original.evidence),
            ("tie/a", "2026-09-02T00:00:00+00:00", original.evidence),
            ("tie/old", "2026-08-01T00:00:00+00:00", original.evidence),
            ("tie/proof", "2025-01-01T00:00:00+00:00", (*original.evidence, original.evidence[0])),
        ]
    ]
    snapshot = runtime.catalog.publish(records, "2026-09-01T00:00:00Z")
    for query in ("", "Equal"):
        result = rank(runtime.catalog, RankRequest(query=query, snapshot=snapshot))
        assert [r.server.id for r in result.results] == ["tie/proof", "tie/a", "tie/b", "tie/old"]
    assert runtime.catalog.publish(list(reversed(records)), "2026-09-01T00:00:00Z") == snapshot


def test_filters_apply_before_result_limit(runtime):
    original = runtime.catalog.records()[0]
    records = [
        original.model_copy(
            update={
                "id": f"filtered/{index:03}",
                "status": "active",
                "name": "Equal",
                "auth": "none" if index == 60 else "oauth",
            }
        )
        for index in range(61)
    ]
    snapshot = runtime.catalog.publish(records, "2026-09-01T00:00:00Z")
    result = rank(
        runtime.catalog,
        RankRequest(
            query="Equal",
            snapshot=snapshot,
            limit=1,
            filters=Filters(auth="none"),
        ),
    )
    assert [r.server.id for r in result.results] == ["filtered/060"]
