import json
import logging
from datetime import UTC, datetime
from threading import Event, Thread
from time import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from mcp_sorter.evaluation import EvaluationReport, evaluate, report_path, save_report
from mcp_sorter.events import record_event
from mcp_sorter.gateways import rank_with_profile
from mcp_sorter.runtime import Runtime
from mcp_sorter.sources import fetch_registry
from mcp_sorter.storage import canonical


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["evaluation", "catalog-refresh"]
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    profile: Literal[
        "baseline",
        "demo-balanced",
        "demo-fast",
        "demo-timeout",
        "demo-malformed",
        "demo-rate-limited",
        "demo-unavailable",
    ] = "baseline"


def enqueue(runtime: Runtime, request: JobRequest) -> dict[str, Any]:
    payload = request.model_dump(exclude={"idempotency_key"})
    payload["snapshot"] = runtime.catalog.active()
    with runtime.engine.begin() as db:
        db.exec_driver_sql("BEGIN IMMEDIATE")
        old = (
            db.execute(
                text("SELECT * FROM jobs WHERE idempotency_key=:key"),
                {"key": request.idempotency_key},
            )
            .mappings()
            .first()
        )
        if old:
            original = json.loads(old["payload"])
            if any(original[k] != payload[k] for k in ("kind", "profile")):
                raise ValueError("Idempotency key already belongs to a different request")
            return dict(old)
        identifier = uuid4().hex
        now = time()
        db.execute(
            text(
                "INSERT INTO jobs (id,idempotency_key,payload,status,attempts,"
                "available_at,created_at) VALUES (:id,:key,:payload,'queued',0,:now,:now)"
            ),
            {
                "id": identifier,
                "key": request.idempotency_key,
                "payload": canonical(payload),
                "now": now,
            },
        )
    return get_job(runtime.engine, identifier)


def get_job(engine: Engine, identifier: str) -> dict[str, Any]:
    with engine.connect() as db:
        row = (
            db.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": identifier})
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("Job not found")
        return dict(row)


def list_jobs(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as db:
        return [
            dict(r)
            for r in db.execute(
                text("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100")
            ).mappings()
        ]


def claim(engine: Engine, now: float | None = None) -> dict[str, Any] | None:
    now = time() if now is None else now
    with engine.begin() as db:
        db.exec_driver_sql("BEGIN IMMEDIATE")
        db.execute(
            text(
                "UPDATE jobs SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,"
                "owner=NULL,error_code='lease-expired' WHERE status='running' AND lease_until<=:now"
            ),
            {"now": now},
        )
        row = (
            db.execute(
                text(
                    "SELECT * FROM jobs WHERE status='queued' AND available_at<=:now "
                    "ORDER BY created_at,id LIMIT 1"
                ),
                {"now": now},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        owner = uuid4().hex
        db.execute(
            text(
                "UPDATE jobs SET status='running', attempts=attempts+1, owner=:owner,"
                "lease_until=:lease WHERE id=:id"
            ),
            {"owner": owner, "lease": now + 30, "id": row["id"]},
        )
        return {
            **row,
            "owner": owner,
            "attempts": row["attempts"] + 1,
            "status": "running",
            "lease_until": now + 30,
        }


def renew(engine: Engine, job: dict[str, Any], now: float | None = None) -> bool:
    now = time() if now is None else now
    with engine.begin() as db:
        return bool(
            db.execute(
                text(
                    "UPDATE jobs SET lease_until=:until WHERE id=:id "
                    "AND owner=:owner AND status='running' AND lease_until>:now"
                ),
                {"id": job["id"], "owner": job["owner"], "until": now + 30, "now": now},
            ).rowcount
        )


def finish(
    runtime: Runtime,
    job: dict[str, Any],
    result: dict[str, Any],
    failed: bool = False,
    now: float | None = None,
) -> bool:
    now = time() if now is None else now
    snapshot = result.get("snapshot") if not failed else None
    if snapshot:
        runtime.catalog.validate(snapshot)
    with runtime.engine.begin() as db:
        db.exec_driver_sql("BEGIN IMMEDIATE")
        status = "queued" if failed and job["attempts"] < 3 else "failed" if failed else "completed"
        changed = db.execute(
            text(
                "UPDATE jobs SET status=:status,result=:result,error_code=:error,"
                "owner=NULL,lease_until=NULL,available_at=:available "
                "WHERE id=:id AND owner=:owner AND status='running' AND lease_until>:now"
            ),
            {
                "status": status,
                "result": canonical(result),
                "error": "execution-failed" if failed else None,
                "available": now + 2 ** job["attempts"],
                "id": job["id"],
                "owner": job["owner"],
                "now": now,
            },
        ).rowcount
        if changed and snapshot:
            expected = json.loads(job["payload"])["snapshot"]
            # A concurrent operator activation wins over an old refresh.
            activated = db.execute(
                text("UPDATE state SET value=:new WHERE key='active_catalog' AND value=:old"),
                {"new": snapshot, "old": expected},
            ).rowcount
            if activated:
                db.execute(
                    text("INSERT INTO activations(snapshot,created_at) VALUES (:id,:at)"),
                    {"id": snapshot, "at": datetime.now(UTC).isoformat()},
                )
    if changed:
        record_event(runtime.engine, "job", status)
    return bool(changed)


def execute(runtime: Runtime, job: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(job["payload"])
    if payload["kind"] == "evaluation":
        path = report_path(runtime.settings.data_dir, job["id"])
        if path.exists():
            EvaluationReport.model_validate_json(path.read_text("utf-8"))
        else:
            report = evaluate(
                runtime.catalog,
                profile=payload["profile"],
                snapshot=payload["snapshot"],
                ranker=lambda catalog, query: rank_with_profile(
                    runtime, query.model_copy(update={"profile": payload["profile"]})
                ),
            )
            report.id = job["id"]
            try:
                save_report(runtime.settings.data_dir, report)
            except FileExistsError:
                EvaluationReport.model_validate_json(path.read_text("utf-8"))
        return {"report_id": job["id"]}
    if runtime.settings.mode == "demo":
        records = runtime.catalog.records(payload["snapshot"])
        records = [
            r.model_copy(update={"version": "1.1.0-demo"}) if r.id == "demo/github" else r
            for r in records
        ]
        as_of = "2026-09-02T00:00:00Z"
    else:
        records, rejected = fetch_registry(runtime.settings)
        if rejected:
            raise ValueError("Incomplete refresh; current catalog retained")
        as_of = datetime.fromtimestamp(job["created_at"], UTC).isoformat()
    return {"snapshot": runtime.catalog.publish(records, as_of)}


def run_once(runtime: Runtime) -> bool:
    job = claim(runtime.engine)
    if job is None:
        return False
    stopped = Event()

    def heartbeat() -> None:
        while not stopped.wait(5):
            if not renew(runtime.engine, job):
                return

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        try:
            result = execute(runtime, job)
        except Exception:
            # Persist bounded codes, never exception text from untrusted providers.
            finish(runtime, job, {}, failed=True)
        else:
            finish(runtime, job, result)
    finally:
        stopped.set()
        thread.join()
    return True


class Worker:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.stopped = Event()
        self.thread = Thread(target=self.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                run_once(self.runtime)
            except SQLAlchemyError:
                logging.getLogger("mcp_sorter.worker").warning(
                    '{"event":"state-store-unavailable"}'
                )
                self.stopped.wait(1)
            self.stopped.wait(0.25)

    def close(self) -> None:
        self.stopped.set()
        self.thread.join()
