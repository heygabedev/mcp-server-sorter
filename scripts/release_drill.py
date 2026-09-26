"""Install two built artifacts offline, switch versions, and verify rollback through HTTP."""

import argparse
import json
import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from mcp_sorter import __version__
from mcp_sorter.recovery import digest
from mcp_sorter.releases import activate_release, directory, python_path, rollback_release, stage
from mcp_sorter.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def smoke(settings, identifier, expected):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    executable = python_path(directory(settings, identifier))
    env = {**os.environ, "SORTER_DATA_DIR": str(settings.data_dir), "SORTER_MODE": "demo"}
    with (settings.data_dir / "drill.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(executable), "-m", "mcp_sorter.cli", "serve", "--port", str(port)],
            stdout=log,
            stderr=log,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2
            ) as client:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        health = client.get("/health/ready")
                        if health.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline or process.poll() is not None:
                        raise RuntimeError("Installed service did not become ready")
                    time.sleep(0.1)
                assert (
                    client.get("/health/live?secret=private-drill-marker").json()["version"]
                    == expected
                )
                assert '<div id="root">' in client.get("/").text
                assert (
                    client.post("/api/v1/rankings", json={"query": "github"}).json()["results"][0][
                        "server"
                    ]["id"]
                    == "demo/github"
                )
                return health.json()["snapshot"]
        finally:
            if os.name == "nt":
                # The Windows venv launcher can start a child interpreter. Stop
                # the owned process tree so its service lock cannot outlive it.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", default="0.1.0")
    parser.add_argument("--candidate", default=__version__)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="sorter-release-drill-") as temporary:
        settings = Settings(data_dir=Path(temporary), worker_enabled=False)
        records = []
        for version in (args.previous, args.candidate):
            wheel = ROOT / "dist" / f"mcp_server_sorter-{version}-py3-none-any.whl"
            record = stage(settings, wheel, digest(wheel), ROOT / "dist" / "wheelhouse")
            records.append(record)
            activate_release(settings, record.sha256)
            snapshot = smoke(settings, record.sha256, version)
        rolled = rollback_release(settings)
        assert rolled.active == records[0].sha256
        assert smoke(settings, rolled.active, args.previous) == snapshot
        assert "private-drill-marker" not in (settings.data_dir / "drill.log").read_text("utf-8")
        report = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "installed_versions": [record.manifest.application_version for record in records],
            "rollback": "passed",
            "packaged_ui": "passed",
            "offline_install": "passed",
            "query_redaction": "passed",
            "snapshot_preserved": snapshot,
            "artifacts": [record.sha256 for record in records],
        }
        (ROOT / "dist" / "release-drill.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
