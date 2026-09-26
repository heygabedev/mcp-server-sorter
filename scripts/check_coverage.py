"""Enforce branch coverage separately from the combined line/branch percentage."""

import json
import sys
from pathlib import Path

CRITICAL = {"ranking.py", "evaluation.py", "recovery.py", "versioning.py", "releases.py"}


def failures(report):
    files = {Path(name.replace("\\", "/")).name: value for name, value in report["files"].items()}
    missing = CRITICAL - files.keys()
    result = [f"Missing coverage for {name}" for name in sorted(missing)]
    for name, summary, minimum in [
        ("overall", report["totals"], 85),
        *((name, files[name]["summary"], 90) for name in sorted(CRITICAL - missing)),
    ]:
        branches = summary["num_branches"]
        percent = 100 * summary["covered_branches"] / branches if branches else 0
        print(f"{name}: {percent:.2f}% branch coverage (minimum {minimum}%)")
        if percent < minimum:
            result.append(f"{name} is below {minimum}% branch coverage")
    return result


if __name__ == "__main__":
    errors = failures(
        json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.json").read_text("utf-8"))
    )
    if errors:
        raise SystemExit("\n".join(errors))
