import json
import math
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
    assert first.summary["unsupported_claim_rate"] is None
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
