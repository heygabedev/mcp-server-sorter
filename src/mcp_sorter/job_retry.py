import json
from time import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from mcp_sorter.jobs import get_job, with_attempts
from mcp_sorter.runtime import Runtime
from mcp_sorter.telemetry import request_trace


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


def retry_job(runtime: Runtime, identifier: str, request: RetryRequest) -> dict[str, object]:
    trace_id, span_id = request_trace()
    with runtime.engine.begin() as db:
        db.exec_driver_sql("BEGIN IMMEDIATE")
        original = (
            db.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": identifier})
            .mappings()
            .first()
        )
        if original is None:
            raise ValueError("Job not found")
        if original["status"] != "failed":
            raise ValueError("Only terminal failed jobs can be retried")
        existing = (
            db.execute(
                text("SELECT * FROM jobs WHERE idempotency_key=:key"),
                {"key": request.idempotency_key},
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if existing["parent_job_id"] != identifier:
                raise ValueError("Idempotency key already belongs to a different request")
            return with_attempts(db, [dict(existing)])[0]
        payload = json.loads(original["payload"])
        required = {"snapshot", "configuration", "profile"}
        if payload["kind"] == "evaluation":
            required |= {"dataset_sha256", "dataset_version", "dataset"}
        if not required <= payload.keys():
            raise ValueError("Legacy job lacks reproducible input pins; submit a new job")
        new_id = uuid4().hex
        now = time()
        db.execute(
            text(
                "INSERT INTO jobs(id,idempotency_key,payload,status,attempts,"
                "available_at,created_at,parent_job_id,request_trace_id,request_span_id) "
                "VALUES (:id,:key,:payload,'queued',0,:now,:now,:parent,:trace,:span)"
            ),
            {
                "id": new_id,
                "key": request.idempotency_key,
                "payload": original["payload"],
                "now": now,
                "parent": identifier,
                "trace": trace_id,
                "span": span_id,
            },
        )
    return get_job(runtime.engine, new_id)
