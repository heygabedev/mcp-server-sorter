import json

import httpx
import pytest
from sqlalchemy import text

from mcp_sorter.gateways import GatewayFailure, mock_response, rank_with_profile, reserve_budget
from mcp_sorter.ranking import RankRequest, rank


@pytest.mark.parametrize(
    "profile,reason",
    [
        ("demo-balanced", None),
        ("demo-fast", None),
        ("demo-timeout", "timeout"),
        ("demo-rate-limited", "rate-limited"),
        ("demo-unavailable", "unavailable"),
        ("demo-malformed", "invalid-schema"),
        ("openrouter", "network-disabled"),
    ],
)
def test_showcase_profiles_never_need_a_network(runtime, profile, reason):
    request = RankRequest(query="database", profile=profile)
    baseline = rank(runtime.catalog, request)
    result = rank_with_profile(runtime, request)
    assert result.fallback_reason == reason
    assert {s.server.id for s in result.results} == {s.server.id for s in baseline.results}
    if reason:
        assert result.results == baseline.results


def test_empty_results_and_browse_do_not_call_a_model(runtime):
    for query in ("", "unsupportedxyz"):
        result = rank_with_profile(runtime, RankRequest(query=query, profile="demo-timeout"))
        assert result.fallback_reason is None


@pytest.fixture
def live_runtime(runtime, monkeypatch):
    runtime.settings.mode = "live"
    runtime.settings.allowed_hosts = ("openrouter.ai", "proxy.example")
    runtime.settings.openrouter_model = "example/model"
    runtime.settings.litellm_model = "deployment"
    runtime.settings.litellm_url = "https://proxy.example/v1/chat/completions"
    runtime.settings.spending_limit_usd = 0.50
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    monkeypatch.setenv("LITELLM_API_KEY", "placeholder")
    return runtime


@pytest.mark.parametrize("profile", ["openrouter", "litellm"])
def test_gateway_request_contract(live_runtime, profile):
    request = RankRequest(query="github", profile=profile)
    baseline = rank(live_runtime.catalog, request)

    def respond(message):
        payload = json.loads(message.content)
        assert payload["response_format"]["json_schema"]["strict"]
        if profile == "openrouter":
            assert payload["provider"]["allow_fallbacks"] is False
        else:
            assert payload["disable_fallbacks"] is True
        return httpx.Response(
            200,
            json={
                "model": "resolved/model",
                "choices": [
                    {"message": {"content": json.dumps(mock_response("demo-balanced", baseline))}}
                ],
            },
        )

    result = rank_with_profile(live_runtime, request, httpx.MockTransport(respond))
    assert result.mode == "live-rerank"
    assert result.model_metadata["returned_model"] == "resolved/model"
    assert result.model_metadata["route_verified"] is False
    with live_runtime.engine.connect() as db:
        assert (
            db.execute(text("SELECT status FROM budget_reservations")).scalar_one() == "completed"
        )


@pytest.mark.parametrize("status,reason", [(429, "rate-limited"), (503, "provider-error")])
def test_provider_failures_keep_the_reservation(live_runtime, status, reason):
    result = rank_with_profile(
        live_runtime,
        RankRequest(query="github", profile="openrouter"),
        httpx.MockTransport(lambda r: httpx.Response(status)),
    )
    assert result.fallback_reason == reason
    with live_runtime.engine.connect() as db:
        assert db.execute(text("SELECT status FROM budget_reservations")).scalar_one() == "reserved"


def test_invalid_model_output_cannot_add_candidates_or_evidence(live_runtime):
    request = RankRequest(query="github", profile="openrouter")
    for payload, reason in [
        (
            {
                "order": [
                    {"id": "invented", "evidence_ids": ["invented"], "reason": "capability-match"}
                ]
            },
            "candidate-set-mismatch",
        ),
        (
            {
                "order": [
                    {
                        "id": "demo/github",
                        "evidence_ids": ["invented"],
                        "reason": "capability-match",
                    }
                ]
            },
            "invalid-evidence",
        ),
        ([], "invalid-schema"),
    ]:
        transport = httpx.MockTransport(
            lambda r, payload=payload: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
            )
        )
        result = rank_with_profile(live_runtime, request, transport)
        assert result.fallback_reason == reason
        assert result.results[0].server.id == "demo/github"


def test_missing_configuration_budget_and_allowlist(live_runtime, monkeypatch):
    request = RankRequest(query="github", profile="openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert rank_with_profile(live_runtime, request).fallback_reason == "provider-not-configured"
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    live_runtime.settings.spending_limit_usd = 0
    assert rank_with_profile(live_runtime, request).fallback_reason == "budget-exhausted"
    live_runtime.settings.allowed_hosts = ()
    assert rank_with_profile(live_runtime, request).fallback_reason == "destination-denied"
    with pytest.raises(GatewayFailure):
        reserve_budget(live_runtime.engine, 0.05, 0.1)


def test_timeout_and_response_size(live_runtime):
    request = RankRequest(query="github", profile="openrouter")

    def timeout(_):
        raise httpx.ReadTimeout("private message")

    assert (
        rank_with_profile(live_runtime, request, httpx.MockTransport(timeout)).fallback_reason
        == "timeout"
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 1_000_001))
    assert (
        rank_with_profile(live_runtime, request, transport).fallback_reason == "response-too-large"
    )
