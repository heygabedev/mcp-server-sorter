"""Save reproducible fixture reports and their paired comparisons for release review."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_sorter.evaluation import compare_reports, evaluate, save_report
from mcp_sorter.gateways import rank_with_profile
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
PROFILES = (
    "baseline",
    "demo-balanced",
    "demo-fast",
    "demo-timeout",
    "demo-malformed",
    "demo-rate-limited",
    "demo-unavailable",
)


def main():
    with TemporaryDirectory(prefix="sorter-fixture-review-") as temporary:
        runtime = Runtime(Settings(data_dir=Path(temporary), worker_enabled=False))
        try:
            reports = []
            for profile in PROFILES:
                report = evaluate(
                    runtime.catalog,
                    profile=profile,
                    ranker=lambda catalog, request, profile=profile: rank_with_profile(
                        runtime, request.model_copy(update={"profile": profile})
                    ),
                )
                save_report(ROOT / "dist/fixture-evaluations", report)
                reports.append(report)
            output = {
                "notice": reports[0].notice,
                "source_sha256": reports[0].source_sha256,
                "dataset_sha256": reports[0].dataset_sha256,
                "snapshot": reports[0].provenance["snapshot"],
                "configuration": reports[0].provenance["configuration"],
                "cases": len(reports[0].cases),
                "profiles": [
                    {
                        "profile": report.profile,
                        "summary": report.summary,
                        "comparison": compare_reports(reports[0], report),
                    }
                    for report in reports
                ],
            }
            (ROOT / "dist/fixture-evaluations.json").write_text(
                json.dumps(output, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(output, indent=2))
        finally:
            runtime.close()


if __name__ == "__main__":
    main()
