"""Build a wheel with the production interface and an explicit release manifest."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:rc\d+)?", args.version):
        parser.error("Use a numeric release or release-candidate version")
    if not args.skip_web:
        npm = shutil.which("npm")
        if npm is None:
            parser.error("Node.js/npm is required to build the interface")
        subprocess.run([npm, "run", "build", "--prefix", "web"], cwd=ROOT, check=True)
    with TemporaryDirectory(prefix="sorter-wheel-") as temporary:
        staging = Path(temporary)
        shutil.copytree(ROOT / "src", staging / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "web" / "dist", staging / "web" / "dist")
        for name in ("LICENSE", "hatch_build.py", "README.md"):
            if (ROOT / name).exists():
                shutil.copyfile(ROOT / name, staging / name)
        config = (ROOT / "pyproject.toml").read_text("utf-8")
        config = re.sub(r'(?m)^version = "[^"]+"', f'version = "{args.version}"', config, count=1)
        (staging / "pyproject.toml").write_text(config, encoding="utf-8")
        manifest = staging / "src" / "mcp_sorter" / "release.json"
        data = json.loads(manifest.read_text("utf-8"))
        data["application_version"] = args.version
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                str(staging),
                "-o",
                str(ROOT / "dist"),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
