import json
import math
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.cli import app
from mcp_sorter.evaluation import (
    compare_reports,
    evaluate,
    load_golden,
    ranking_metrics,
    render_saved_report,
    report_path,
    save_report,
    summarize,
    validate_golden,
)
from mcp_sorter.models import Filters
from mcp_sorter.ranking import RankRequest, rank
from mcp_sorter.settings import Settings


def test_metrics_against_analytical_examples():
    metrics = ranking_metrics(["wrong", "right"], {"right": 3})
    assert metrics["ndcg_at_5"] == pytest.approx(1 / math.log2(3))
    assert metrics["recall_at_10"] == 1
    assert metrics["reciprocal_rank"] == 0.5
    assert ranking_metrics([], {"right": 3})["reciprocal_rank"] == 0
    assert ranking_metrics([], {})["ndcg_at_5"] is None
    with pytest.raises(ValueError):
        ranking_metrics(["duplicate", "duplicate"], {"duplicate": 1})
    mixed = ranking_metrics(["low", "high"], {"low": 1, "high": 3})
    assert mixed["ndcg_at_5"] == pytest.approx((1 + 7 / math.log2(3)) / (7 + 1 / math.log2(3)))
    late = ranking_metrics([f"wrong-{i}" for i in range(9)] + ["right"], {"right": 3})
    assert late["recall_at_10"] == 1 and late["ndcg_at_5"] == 0


def test_golden_dataset_shape_and_leakage_guards():
    dataset = load_golden()
    assert len(dataset.cases) == 90
    assert sum(c.split == "development" for c in dataset.cases) == 30
    assert sum(c.split == "heldout" for c in dataset.cases) == 60
    invalid = dataset.model_copy(deep=True)
    invalid.cases.append(invalid.cases[0])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_golden(invalid)
    for field, value, error in [
        ("intent_id", dataset.cases[0].intent_id, "leakage"),
        ("relevance", {"demo/github": 7}, "grades"),
        ("expect_abstention", True, "Abstention"),
        ("expected_top", ["absent"], "Expected"),
    ]:
        invalid = dataset.model_copy(deep=True)
        setattr(invalid.cases[30], field, value)
        with pytest.raises(ValueError, match=error):
            validate_golden(invalid)


def test_golden_run_is_repeatable_and_cites_only_known_evidence(runtime):
    first = evaluate(runtime.catalog)
    second = evaluate(runtime.catalog)
    assert [c.model_dump(exclude={"latency_ms"}) for c in first.cases] == [
        c.model_dump(exclude={"latency_ms"}) for c in second.cases
    ]
    assert first.summary["constraint_violations"] == 0
    assert first.summary["invalid_evidence_references"] == 0
    assert first.summary["abstention_accuracy"] == 1
    assert first.summary["unsupported_claim_rate"] == 0
    assert set(first.slices) == {"development", "heldout"}
    comparison = compare_reports(first, second)
    assert comparison["passes_regression_gate"]
    assert comparison["ci95"] == [0, 0]
    baseline = json.loads(
        files("mcp_sorter.data").joinpath("evaluation_baseline.json").read_text("utf-8")
    )
    assert first.dataset_sha256 == baseline["dataset_sha256"]
    assert first.provenance["snapshot"] == baseline["snapshot"]
    assert first.summary["ndcg_at_5"] >= baseline["metrics"]["ndcg_at_5"] - 0.02


def test_unsupported_explanations_and_unknown_results_fail_evaluation(runtime):
    def poisoned(catalog, request):
        ranking = rank(catalog, request)
        if ranking.results:
            ranking.results[0].reasons.append("Guaranteed secure in every environment")
            ranking.results[0].server = ranking.results[0].server.model_copy(
                update={"id": "absent"}
            )
        return ranking

    baseline = evaluate(runtime.catalog)
    report = evaluate(runtime.catalog, ranker=poisoned)
    assert report.summary["unsupported_claim_rate"] > 0
    assert report.summary["invalid_evidence_references"] > 0
    assert not compare_reports(baseline, report)["passes_regression_gate"]
    assert report.provenance["python_version"]
    assert report.created_at and report.summary["p95_latency_ms"] > 0


def test_regression_gate_catches_degraded_ranking(runtime):
    baseline = evaluate(runtime.catalog)
    candidate = baseline.model_copy(deep=True)
    for case in candidate.cases:
        if case.metrics["ndcg_at_5"] is not None:
            case.metrics["ndcg_at_5"] = 0
    candidate.summary = summarize(candidate.cases)
    assert not compare_reports(baseline, candidate)["passes_regression_gate"]
    candidate.dataset_sha256 = "different"
    with pytest.raises(ValueError, match="identical datasets"):
        compare_reports(baseline, candidate)
    candidate = baseline.model_copy(deep=True)
    candidate.cases.pop()
    with pytest.raises(ValueError, match="case IDs"):
        compare_reports(baseline, candidate)


@pytest.mark.parametrize("behavior", ["never", "always"])
def test_abstention_gate_uses_cases_instead_of_cached_summary(runtime, behavior):
    def broken(catalog, request):
        result = rank(catalog, request)
        if behavior == "never" and not result.results:
            result = rank(catalog, request.model_copy(update={"query": "github"}))
        if behavior == "always":
            result.results = []
        return result

    baseline = evaluate(runtime.catalog)
    candidate = evaluate(runtime.catalog, ranker=broken)
    assert sum(not c.abstention_correct for c in candidate.cases) == (
        30 if behavior == "never" else 60
    )
    candidate.summary = baseline.summary
    assert not compare_reports(baseline, candidate)["passes_regression_gate"]


def test_comparison_rejects_empty_and_duplicate_reports(runtime):
    report = evaluate(runtime.catalog)
    for cases, message in [([], "nonempty"), ([report.cases[0]] * 2, "unique")]:
        with pytest.raises(ValueError, match=message):
            compare_reports(report, report.model_copy(update={"cases": cases}))


def test_comparison_detects_a_split_regression_hidden_by_the_average(runtime):
    baseline = evaluate(runtime.catalog)
    candidate = baseline.model_copy(deep=True)
    for case in candidate.cases:
        if case.split == "development" and case.metrics["ndcg_at_5"] is not None:
            case.metrics["ndcg_at_5"] = 0.95
    result = compare_reports(baseline, candidate)
    assert result["ndcg_delta"] > -0.02
    assert result["failures"] == [{"scope": "development", "code": "ndcg-regression"}]
    assert not result["passes_regression_gate"]
    candidate.cases[0].split = "heldout"
    with pytest.raises(ValueError, match="split"):
        compare_reports(baseline, candidate)


@pytest.mark.parametrize("decrease,passed", [(0.02, True), (0.020001, False)])
def test_ndcg_gate_threshold(runtime, decrease, passed):
    baseline = evaluate(runtime.catalog)
    candidate = baseline.model_copy(deep=True)
    for case in candidate.cases:
        if case.metrics["ndcg_at_5"] is not None:
            case.metrics["ndcg_at_5"] -= decrease
    assert compare_reports(baseline, candidate)["passes_regression_gate"] is passed


def test_gate_failures_reach_api_and_cli(runtime, monkeypatch):
    baseline = evaluate(runtime.catalog)
    candidate = baseline.model_copy(deep=True)
    candidate.id = "f" * 32
    candidate.cases[0].abstention_correct = False
    save_report(runtime.settings.data_dir, baseline)
    save_report(runtime.settings.data_dir, candidate)
    monkeypatch.setenv("SORTER_DATA_DIR", str(runtime.settings.data_dir))
    result = CliRunner().invoke(app, ["eval", "compare", baseline.id, candidate.id])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["gate_policy_version"] == "fixture-regression-v2"
    with TestClient(create_app(runtime.settings)) as http:
        result = http.get(
            "/api/v1/evaluation-comparison",
            params={"baseline": baseline.id, "candidate": candidate.id},
        )
        assert result.status_code == 200
        assert {"scope": "overall", "code": "incorrect-abstention"} in result.json()["failures"]


def test_reports_are_immutable_and_html_is_escaped(runtime, tmp_path):
    report = evaluate(runtime.catalog)
    report.profile = "<script>alert(1)</script>"
    save_report(tmp_path, report)
    with pytest.raises(FileExistsError):
        save_report(tmp_path, report)
    rendered = report_path(tmp_path, report.id).with_suffix(".html").read_text("utf-8")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    with pytest.raises(ValueError):
        report_path(tmp_path, "../escape")
    with pytest.raises(ValueError):
        summarize([])


def test_html_repairs_are_atomic_and_leave_json_unchanged(runtime, tmp_path, monkeypatch):
    from pathlib import Path

    report = evaluate(runtime.catalog)
    save_report(tmp_path, report)
    path = report_path(tmp_path, report.id)
    original = path.read_bytes()
    output = path.with_suffix(".html")
    expected = output.read_bytes()
    output.write_text("interrupted")
    replace = Path.replace

    def fail(self, target):
        raise OSError("interrupted before publication")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        render_saved_report(tmp_path, report.id)
    assert output.read_text() == "interrupted"
    assert not list(path.parent.glob(".*.tmp"))
    monkeypatch.setattr(Path, "replace", replace)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: render_saved_report(tmp_path, report.id), range(8)))
    assert path.read_bytes() == original
    assert output.read_bytes() == expected
    path.write_text("corrupt JSON")
    with pytest.raises(ValueError):
        render_saved_report(tmp_path, report.id)
    assert output.read_bytes() == expected
    path.write_text(report.model_copy(update={"id": "a" * 32}).model_dump_json())
    with pytest.raises(ValueError, match="filename"):
        render_saved_report(tmp_path, report.id)


def test_interruption_before_json_publication_leaves_no_report(runtime, tmp_path, monkeypatch):
    def interrupt(*args):
        raise OSError("interrupted")

    monkeypatch.setattr("mcp_sorter.artifacts.os.link", interrupt)
    report = evaluate(runtime.catalog)
    with pytest.raises(OSError):
        save_report(tmp_path, report)
    assert list((tmp_path / "reports").iterdir()) == []


def test_absent_judgments_and_all_abstention_dataset(runtime):
    dataset = load_golden()
    dataset.cases[0].relevance = {"missing": 3}
    dataset.cases[0].expected_top = ["missing"]
    with pytest.raises(ValueError, match="absent"):
        evaluate(runtime.catalog, dataset)
    dataset = load_golden()
    dataset.cases = [case for case in dataset.cases if case.expect_abstention]
    report = evaluate(runtime.catalog, dataset)
    assert report.summary["ndcg_at_5"] is None
    assert compare_reports(report, report)["ci95"] is None


def test_adversarial_queries_preserve_constraints(runtime):
    data = json.loads(files("mcp_sorter.data").joinpath("adversarial.json").read_text("utf-8"))
    for case in data["cases"]:
        result = rank(
            runtime.catalog, RankRequest(query=case["query"], filters=Filters(auth="none"))
        )
        assert all(item.server.auth == "none" for item in result.results)
        if case["assertion"] == "abstain":
            assert not result.results


def test_evaluation_api_and_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTER_DATA_DIR", str(tmp_path))
    with TestClient(create_app(Settings(data_dir=tmp_path))) as http:
        report = http.post("/api/v1/evaluations").json()
        assert len(report["cases"]) == 90
        assert http.get(f"/api/v1/evaluations/{report['id']}").status_code == 200
        assert len(http.get("/api/v1/evaluations").json()) == 1
        assert http.get("/api/v1/evaluations/missing").status_code == 404
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "run"])
    assert result.exit_code == 0, result.output
    second = json.loads(result.stdout)
    assert runner.invoke(app, ["eval", "compare", report["id"], second["id"]]).exit_code == 0
