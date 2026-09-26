import hashlib
import html
import json
import math
import random
import re
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mcp_sorter.catalog import Catalog
from mcp_sorter.models import Filters
from mcp_sorter.ranking import Ranking, RankRequest, rank
from mcp_sorter.storage import canonical


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    intent_id: str
    split: Literal["development", "heldout"]
    query: str
    filters: Filters
    relevance: dict[str, int]
    expected_top: list[str]
    expect_abstention: bool
    evidence_ids: list[str]
    rationale: str
    review_status: Literal["fixture-authored", "maintainer-reviewed"]


class GoldenDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    version: str
    kind: Literal["synthetic-regression"]
    notice: str
    catalog_source_sha256: str
    cases: list[GoldenCase]


def load_golden() -> GoldenDataset:
    data = files("mcp_sorter.data").joinpath("golden.json").read_text("utf-8")
    dataset = GoldenDataset.model_validate_json(data)
    source = json.loads(files("mcp_sorter.data").joinpath("catalog.json").read_text("utf-8"))
    if hashlib.sha256(canonical(source).encode()).hexdigest() != dataset.catalog_source_sha256:
        raise ValueError("The golden dataset was authored against a different fixture catalog")
    validate_golden(dataset)
    return dataset


def validate_golden(dataset: GoldenDataset) -> None:
    if len({case.id for case in dataset.cases}) != len(dataset.cases):
        raise ValueError("Duplicate evaluation case IDs")
    development = {c.intent_id for c in dataset.cases if c.split == "development"}
    heldout = {c.intent_id for c in dataset.cases if c.split == "heldout"}
    if development & heldout:
        raise ValueError("Intent leakage between development and held-out cases")
    for case in dataset.cases:
        if any(grade not in (1, 2, 3) for grade in case.relevance.values()):
            raise ValueError("Relevance grades must be between one and three")
        if case.expect_abstention != (not case.relevance):
            raise ValueError("Abstention label conflicts with relevance judgments")
        if not set(case.expected_top) <= case.relevance.keys():
            raise ValueError("Expected top result is missing a relevance judgment")


def ranking_metrics(ids: list[str], relevance: dict[str, int]) -> dict[str, float | None]:
    if len(ids) != len(set(ids)):
        raise ValueError("Ranking contains duplicate IDs")
    if not relevance:
        return {"ndcg_at_5": None, "recall_at_10": None, "reciprocal_rank": None}

    def dcg(grades: list[int]) -> float:
        return float(
            sum((2.0**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))
        )

    ideal = dcg(sorted(relevance.values(), reverse=True)[:5])
    return {
        "ndcg_at_5": dcg([relevance.get(i, 0) for i in ids[:5]]) / ideal,
        "recall_at_10": len(set(ids[:10]) & relevance.keys()) / len(relevance),
        "reciprocal_rank": next(
            (1 / i for i, identifier in enumerate(ids, 1) if identifier in relevance), 0.0
        ),
    }


class CaseResult(BaseModel):
    id: str
    intent_id: str
    split: str
    result_ids: list[str]
    metrics: dict[str, float | None]
    constraint_violations: int
    invalid_evidence_references: int
    abstention_correct: bool
    expected_top_correct: bool
    fallback: bool
    latency_ms: float


class EvaluationReport(BaseModel):
    schema_version: int = 1
    id: str
    dataset_version: str
    dataset_sha256: str
    source_sha256: str
    execution_mode: str = "fixture"
    profile: str = "baseline"
    provenance: dict[str, Any]
    cases: list[CaseResult]
    summary: dict[str, float | None]
    slices: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    notice: str = "Fixture regression results. Live model quality is unmeasured."


def summarize(cases: list[CaseResult]) -> dict[str, float | None]:
    if not cases:
        raise ValueError("An evaluation must contain at least one case")
    result: dict[str, float | None] = {}
    for metric in ("ndcg_at_5", "recall_at_10", "reciprocal_rank"):
        values = [c.metrics[metric] for c in cases if c.metrics[metric] is not None]
        result[metric] = sum(v for v in values if v is not None) / len(values) if values else None
    result.update(
        {
            "constraint_violations": float(sum(c.constraint_violations for c in cases)),
            "invalid_evidence_references": float(sum(c.invalid_evidence_references for c in cases)),
            "abstention_accuracy": sum(c.abstention_correct for c in cases) / len(cases),
            "expected_top_accuracy": sum(c.expected_top_correct for c in cases) / len(cases),
            "fallback_rate": sum(c.fallback for c in cases) / len(cases),
            "mean_latency_ms": sum(c.latency_ms for c in cases) / len(cases),
            "cost_usd": 0.0,
            "unsupported_claim_rate": None,
        }
    )
    return result


def source_digest() -> str:
    root = Path(__file__).parent
    source = "".join(
        path.relative_to(root).as_posix() + path.read_text("utf-8")
        for path in sorted(root.rglob("*.py"))
    )
    return hashlib.sha256(source.encode()).hexdigest()


def evaluate(
    catalog: Catalog,
    dataset: GoldenDataset | None = None,
    profile: str = "baseline",
    ranker: Callable[[Catalog, RankRequest], Ranking] = rank,
) -> EvaluationReport:
    dataset = dataset or load_golden()
    validate_golden(dataset)
    snapshot = catalog.active()
    records = {record.id: record for record in catalog.records(snapshot)}
    if any(identifier not in records for case in dataset.cases for identifier in case.relevance):
        raise ValueError("Golden judgments reference a server absent from the active snapshot")
    results = []
    provenance: dict[str, Any] = {}
    for case in dataset.cases:
        started = perf_counter()
        ranking = ranker(
            catalog, RankRequest(query=case.query, filters=case.filters, snapshot=snapshot)
        )
        ids = [item.server.id for item in ranking.results]
        invalid = sum(
            not set(item.evidence_ids) <= {e.id for e in records[item.server.id].evidence}
            for item in ranking.results
        )
        results.append(
            CaseResult(
                id=case.id,
                intent_id=case.intent_id,
                split=case.split,
                result_ids=ids,
                metrics=ranking_metrics(ids, case.relevance),
                constraint_violations=sum(
                    not case.filters.matches(item.server) for item in ranking.results
                ),
                invalid_evidence_references=invalid,
                abstention_correct=(not ids) == case.expect_abstention,
                expected_top_correct=(
                    not ids if not case.expected_top else bool(ids) and ids[0] in case.expected_top
                ),
                fallback=ranking.fallback_reason is not None,
                latency_ms=(perf_counter() - started) * 1000,
            )
        )
        provenance = ranking.model_dump(exclude={"results", "query", "filters", "fallback_reason"})
    return EvaluationReport(
        id=uuid4().hex,
        dataset_version=dataset.version,
        dataset_sha256=hashlib.sha256(canonical(dataset.model_dump()).encode()).hexdigest(),
        source_sha256=source_digest(),
        profile=profile,
        provenance=provenance,
        cases=results,
        summary=summarize(results),
        slices={
            split: summarize([case for case in results if case.split == split])
            for split in {case.split for case in results}
        },
    )


def report_path(directory: Path, identifier: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid evaluation ID")
    return directory / "reports" / f"{identifier}.json"


def save_report(directory: Path, report: EvaluationReport) -> None:
    path = report_path(directory, report.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(report.model_dump_json(indent=2))
    rows = "".join(
        f"<tr><td>{html.escape(c.id)}</td><td>{html.escape(', '.join(c.result_ids))}</td>"
        f"<td>{c.metrics['ndcg_at_5']}</td></tr>"
        for c in report.cases
    )
    path.with_suffix(".html").write_text(
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Evaluation report</title>"
        f"<h1>Evaluation: {html.escape(report.profile)}</h1><p>{html.escape(report.notice)}</p>"
        "<table><thead><tr><th>Case</th><th>Results</th><th>NDCG@5</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></html>",
        encoding="utf-8",
    )


def compare_reports(baseline: EvaluationReport, candidate: EvaluationReport) -> dict[str, object]:
    if (
        baseline.dataset_sha256 != candidate.dataset_sha256
        or baseline.provenance["snapshot"] != candidate.provenance["snapshot"]
    ):
        raise ValueError("Paired comparisons require identical datasets and snapshots")
    old = {case.id: case for case in baseline.cases}
    if old.keys() != {case.id for case in candidate.cases}:
        raise ValueError("Paired comparisons require identical case IDs")
    differences: list[float] = []
    groups: dict[str, list[float]] = {}
    for case in candidate.cases:
        new_score = case.metrics["ndcg_at_5"]
        old_score = old[case.id].metrics["ndcg_at_5"]
        if new_score is None and old_score is None:
            continue
        if new_score is None or old_score is None:
            raise ValueError("Paired cases have incompatible relevance metrics")
        difference = new_score - old_score
        differences.append(difference)
        groups.setdefault(case.intent_id, []).append(difference)
    rng = random.Random(42)
    group_values = [sum(values) / len(values) for values in groups.values()]
    boot = (
        sorted(
            sum(rng.choices(group_values, k=len(group_values))) / len(group_values)
            for _ in range(1000)
        )
        if group_values
        else []
    )
    delta = sum(differences) / len(differences) if differences else None
    passed = (delta is None or delta >= -0.02) and all(
        candidate.summary[key] == 0
        for key in ("constraint_violations", "invalid_evidence_references")
    )
    return {
        "ndcg_delta": delta,
        "ci95": [boot[24], boot[974]] if boot else None,
        "passes_regression_gate": passed,
        "bootstrap_unit": "intent",
        "mode": "fixture",
    }
