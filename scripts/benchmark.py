"""Exercise real HTTP with an open-loop load against 10,000 synthetic servers."""

import argparse
import asyncio
import json
import math
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from mcp_sorter.evaluation import source_digest
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def rss(pid):
    path = Path(f"/proc/{pid}/status")
    if not path.exists():
        return None
    return next(
        int(line.split()[1]) * 1024
        for line in path.read_text().splitlines()
        if line.startswith("VmRSS:")
    )


async def phase(url, rate, seconds, pid):
    queries = ["", "tools", "github", "database", "messages", "zznotpresent"]
    latencies, errors = [], []
    initial_rss = rss(pid)
    started = time.perf_counter()
    async with httpx.AsyncClient(
        base_url=url, trust_env=False, timeout=30, limits=httpx.Limits(max_connections=100)
    ) as client:

        async def request(index, scheduled):
            try:
                filters = {"auth": "none"} if index % 3 == 0 else {}
                response = await client.post(
                    "/api/v1/rankings",
                    json={
                        "query": queries[index % len(queries)],
                        "filters": filters,
                    },
                )
                response.raise_for_status()
                results = response.json()["results"]
                assert len(results) <= 20
                assert all(r["server"]["status"] != "deprecated" for r in results)
                assert not filters or all(r["server"]["auth"] == "none" for r in results)
            except (httpx.HTTPError, ValueError, KeyError, AssertionError) as error:
                errors.append(type(error).__name__)
            finally:
                # Include client scheduling delay; do not hide overload with closed-loop timing.
                latencies.append((time.perf_counter() - scheduled) * 1000)

        tasks = []
        for index in range(round(rate * seconds)):
            scheduled = started + index / rate
            await asyncio.sleep(max(0, scheduled - time.perf_counter()))
            tasks.append(asyncio.create_task(request(index, scheduled)))
        await asyncio.gather(*tasks)
    ordered = sorted(latencies)
    return {
        "scheduled_rps": rate,
        "duration_seconds": seconds,
        "requests": len(latencies),
        "elapsed_seconds": time.perf_counter() - started,
        "p50_ms": ordered[math.ceil(len(ordered) * 0.5) - 1],
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "p99_ms": ordered[math.ceil(len(ordered) * 0.99) - 1],
        "errors": len(errors),
        "error_types": sorted(set(errors)),
        "rss_before_bytes": initial_rss,
        "rss_after_bytes": rss(pid),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--load-seconds", type=int, default=30)
    parser.add_argument("--stress-seconds", type=int, default=10)
    parser.add_argument("--soak-seconds", type=int, default=180)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/benchmark.json")
    args = parser.parse_args()
    if min(args.load_seconds, args.stress_seconds, args.soak_seconds) < 1:
        parser.error("Each phase must run for at least one second")
    with TemporaryDirectory(prefix="sorter-benchmark-") as temporary:
        directory = Path(temporary)
        runtime = Runtime(Settings(data_dir=directory, worker_enabled=False))
        try:
            fixture = runtime.catalog.records()
            records = [
                fixture[index % len(fixture)].model_copy(
                    update={
                        "id": f"benchmark/server-{index:05d}",
                    }
                )
                for index in range(10_000)
            ]
            snapshot = runtime.catalog.publish(records, "2026-09-01T00:00:00Z")
            runtime.catalog.activate(snapshot)
        finally:
            runtime.close()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        with (directory / "service.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "mcp_sorter.api:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                env={
                    **os.environ,
                    "SORTER_DATA_DIR": str(directory),
                    "SORTER_MODE": "demo",
                    "SORTER_WORKER_ENABLED": "false",
                },
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                with httpx.Client(base_url=url, trust_env=False, timeout=2) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            if client.get("/health/ready").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Benchmark service did not become ready")
                        time.sleep(0.1)
                    for _ in range(5):
                        client.post("/api/v1/rankings", json={"query": "tools"}).raise_for_status()
                phases = {}
                for name, rate, duration in (
                    ("load", 10, args.load_seconds),
                    ("stress", 50, args.stress_seconds),
                    ("soak", 10, args.soak_seconds),
                ):
                    phases[name] = asyncio.run(phase(url, rate, duration, process.pid))
                    print(json.dumps({name: phases[name]}), flush=True)
                passed = all(
                    phases[name]["errors"] == 0 and phases[name]["p95_ms"] < 500
                    for name in ("load", "soak")
                )
                report = {
                    "created_at": datetime.now(UTC).isoformat(),
                    "platform": platform.platform(),
                    "processor": platform.processor(),
                    "logical_cpus": os.cpu_count(),
                    "python": platform.python_version(),
                    "sqlite": sqlite3.sqlite_version,
                    "source_sha256": source_digest(),
                    "snapshot": snapshot,
                    "servers": 10_000,
                    "workload": "Repeated synthetic records; six queries, one-third filtered",
                    "timing": "Real HTTP; latency includes open-loop scheduling delay",
                    "phases": phases,
                    "passes_latency_gate": passed,
                }
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                if not passed:
                    raise SystemExit("10 RPS load/soak latency or correctness gate failed")
            finally:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                else:
                    process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
