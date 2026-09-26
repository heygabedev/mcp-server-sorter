"""Run a bounded, explicit mutation campaign in disposable source copies."""

import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_sorter.evaluation import source_digest

ROOT = Path(__file__).resolve().parents[1]
# These faults cover retrieval constraints, ordering, metric math, and recovery guards.
# They are a targeted campaign, not an exhaustive mutation score for the repository.
MUTATIONS = [
    (
        "ranking",
        'category", "transport", "auth", "deployment',
        'category", "transport", "deployment',
        "Drop the authentication filter",
    ),
    (
        "ranking",
        "$.status') != 'deprecated'",
        "$.status') = 'deprecated'",
        "Invert the deprecation filter",
    ),
    ("ranking", "$.evidence')) DESC", "$.evidence')) ASC", "Prefer less evidence"),
    ("ranking", "$.updated_at')) DESC", "$.updated_at')) ASC", "Prefer stale evidence"),
    ("evaluation", "2.0**grade - 1", "float(grade)", "Use linear relevance gains"),
    ("evaluation", "len(set(ids[:10])", "len(set(ids[:5])", "Truncate recall at five"),
    (
        "evaluation",
        "(1 / i for i, identifier",
        "(1.0 for i, identifier",
        "Ignore reciprocal position",
    ),
    ("evaluation", "delta >= -0.02", "delta >= -1.02", "Accept a severe regression"),
    ("recovery", "if digest(artifact) != expected:", "if False:", "Skip backup checksums"),
    (
        "recovery",
        "if SAFE_FILE.fullmatch(name) is None or not re.fullmatch",
        "if SAFE_FILE.fullmatch(name) is None and not re.fullmatch",
        "Weaken backup path validation",
    ),
    ("recovery", "with service_lock(current):", "if True:", "Restore while the service is running"),
    ("releases", "if digest(wheel) != sha256:", "if False:", "Ignore the pinned artifact digest"),
    (
        "releases",
        "not in record.manifest.readable_state_revisions",
        "in record.manifest.readable_state_revisions",
        "Invert rollback compatibility",
    ),
]


def run_tests(directory, modules):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "--disable-warnings",
            *[f"tests/test_{module}.py" for module in sorted(modules)],
        ],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": str(directory / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )


def main():
    results = []
    with TemporaryDirectory(prefix="sorter-mutations-") as temporary:
        directory = Path(temporary)
        shutil.copytree(
            ROOT / "src", directory / "src", ignore=shutil.ignore_patterns("__pycache__")
        )
        shutil.copytree(
            ROOT / "tests", directory / "tests", ignore=shutil.ignore_patterns("__pycache__")
        )
        shutil.copyfile(ROOT / "pyproject.toml", directory / "pyproject.toml")
        modules = {item[0] for item in MUTATIONS}
        baseline = run_tests(directory, modules)
        if baseline.returncode != 0:
            raise SystemExit(
                "Unmodified mutation baseline failed:\n" + baseline.stdout + baseline.stderr
            )
        for module, before, after, description in MUTATIONS:
            path = directory / "src/mcp_sorter" / f"{module}.py"
            original = path.read_text("utf-8")
            if original.count(before) != 1:
                raise SystemExit(f"Mutation no longer has one exact location: {description}")
            mutated = original.replace(before, after)
            ast.parse(mutated)
            path.write_text(mutated, encoding="utf-8")
            try:
                result = run_tests(directory, {module})
                status = {0: "survived", 1: "killed"}.get(result.returncode, "invalid")
                # Collection/import errors are invalid, never credit for detection.
                if "ERROR collecting" in result.stdout:
                    status = "invalid"
                results.append({"module": module, "fault": description, "status": status})
                print(f"{status}: {description}", flush=True)
            except subprocess.TimeoutExpired:
                results.append({"module": module, "fault": description, "status": "timeout"})
            finally:
                path.write_text(original, encoding="utf-8")
        killed = sum(item["status"] == "killed" for item in results)
        score = killed / len(results)
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "source_sha256": source_digest(),
            "scope": "13 targeted retrieval, evaluation, and recovery mutations",
            "exclusions": [],
            "results": results,
            "detection_rate": score,
            "passes_gate": score >= 0.8
            and all(r["status"] in {"killed", "survived"} for r in results),
        }
        (ROOT / "dist").mkdir(exist_ok=True)
        (ROOT / "dist/mutation-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        if not report["passes_gate"]:
            raise SystemExit("Targeted mutation detection gate failed")


if __name__ == "__main__":
    main()
