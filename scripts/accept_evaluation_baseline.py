"""Recalculate the fixture baseline; review the resulting diff before committing."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_sorter.evaluation import evaluate
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings


def main() -> None:
    with TemporaryDirectory() as directory:
        runtime = Runtime(Settings(data_dir=Path(directory)))
        try:
            report = evaluate(runtime.catalog)
            baseline = {
                "dataset_sha256": report.dataset_sha256,
                "snapshot": report.provenance["snapshot"],
                "policy_version": report.provenance["policy_version"],
                "metrics": {
                    key: value for key, value in report.summary.items() if "latency" not in key
                },
                "notice": report.notice,
            }
            path = (
                Path(__file__).resolve().parents[1] / "src/mcp_sorter/data/evaluation_baseline.json"
            )
            path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(json.dumps(baseline, indent=2))
        finally:
            runtime.close()


if __name__ == "__main__":
    main()
