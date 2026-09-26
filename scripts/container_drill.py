"""Verify versioned container artifacts, shared data, backup, and rollback through HTTP."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def docker(*arguments, timeout=120):
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=True, timeout=timeout
    ).stdout.strip()


def main():
    versions = ["0.1.0rc1", "0.1.0", "0.1.0rc1"]
    identifiers = {
        version: docker("image", "inspect", f"mcp-server-sorter:{version}", "--format", "{{.Id}}")
        for version in set(versions)
    }
    volume = "sorter-drill-" + uuid4().hex
    docker("volume", "create", volume)
    collection_id = None
    snapshot = None
    previous_backup = None
    try:
        for version in versions:
            image = identifiers[version]
            if previous_backup:
                verified = json.loads(
                    docker(
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--mount",
                        f"type=volume,src={volume},dst=/data,readonly",
                        "--entrypoint",
                        "mcp-sorter",
                        image,
                        "backup",
                        "verify",
                        previous_backup,
                    )
                )
                assert verified["state_revision"] == "0005"
            name = "sorter-drill-" + uuid4().hex
            docker(
                "run",
                "--detach",
                "--name",
                name,
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                f"type=volume,src={volume},dst=/data",
                "--publish",
                "127.0.0.1::8000",
                image,
            )
            try:
                address = docker("port", name, "8000/tcp")
                assert address.startswith("127.0.0.1:")
                with httpx.Client(
                    base_url="http://" + address, trust_env=False, timeout=5
                ) as client:
                    deadline = time.monotonic() + 60
                    while True:
                        try:
                            ready = client.get("/health/ready")
                            if ready.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise RuntimeError("Container did not become ready")
                        time.sleep(0.2)
                    assert (
                        client.get("/health/live?secret=container-private-marker").json()["version"]
                        == version
                    )
                    assert '<div id="root">' in client.get("/").text
                    if collection_id is None:
                        snapshot = ready.json()["snapshot"]
                        response = client.post(
                            "/api/v1/collections",
                            json={
                                "name": "Container rollback drill",
                                "ids": ["demo/github"],
                                "snapshot": snapshot,
                            },
                        )
                        response.raise_for_status()
                        collection_id = response.json()["id"]
                    else:
                        assert ready.json()["snapshot"] == snapshot
                        assert client.get("/api/v1/collections").json()[0]["id"] == collection_id
                    assert client.post("/api/v1/rankings", json={"query": "github"}).json()[
                        "results"
                    ]
                previous_backup = docker("exec", name, "mcp-sorter", "backup", "create")
                assert previous_backup.startswith("/data/backups/")
                logs = subprocess.run(
                    ["docker", "logs", name],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                assert "container-private-marker" not in logs.stdout + logs.stderr
                print(f"Verified container version {version}", flush=True)
            finally:
                docker("rm", "--force", name)
        report = {
            "engine": docker("version", "--format", "{{.Server.Version}}"),
            "platform": docker(
                "image", "inspect", identifiers["0.1.0"], "--format", "{{.Os}}/{{.Architecture}}"
            ),
            "versions": versions,
            "image_ids": identifiers,
            "rollback": "passed",
            "collections_preserved": True,
            "snapshot_preserved": snapshot,
            "backup_compatibility": "passed",
            "packaged_ui": "passed",
            "read_only_root": True,
            "loopback_binding": True,
            "query_redaction": "passed",
        }
        (ROOT / "dist").mkdir(exist_ok=True)
        (ROOT / "dist/container-drill.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        docker("volume", "rm", volume)


if __name__ == "__main__":
    main()
