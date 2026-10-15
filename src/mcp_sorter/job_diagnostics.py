from typing import Literal, get_args

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

FailureCode = Literal[
    "execution-failed",
    "timeout",
    "rate-limited",
    "provider-unavailable",
    "invalid-data",
    "state-store-unavailable",
    "lease-expired",
    "worker-interrupted",
    "retry-budget-exhausted",
    "artifact-unavailable",
]
FAILURE_CODES = frozenset(get_args(FailureCode))


def failure_code(error: Exception) -> FailureCode:
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, SQLAlchemyError):
        return "state-store-unavailable"
    if isinstance(error, httpx.HTTPStatusError):
        return "rate-limited" if error.response.status_code == 429 else "provider-unavailable"
    if isinstance(error, (httpx.TransportError, ConnectionError)):
        return "provider-unavailable"
    if isinstance(error, FileNotFoundError):
        return "artifact-unavailable"
    if isinstance(error, OSError):
        return "state-store-unavailable"
    if isinstance(error, ValueError):
        return "invalid-data"
    return "execution-failed"


class JobAttempt(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    attempt: int = Field(ge=1, le=3)
    started_at: float
    finished_at: float | None
    outcome: Literal["running", "completed", "failed", "lease-expired", "interrupted"]
    error_code: FailureCode | None
    trace_id: str | None = None


class JobView(BaseModel):
    id: str
    idempotency_key: str
    payload: str
    status: Literal["queued", "running", "completed", "failed"]
    attempts: int
    owner: str | None
    lease_until: float | None
    available_at: float
    created_at: float
    result: str | None
    error_code: FailureCode | None
    parent_job_id: str | None
    request_trace_id: str | None
    request_span_id: str | None
    attempt_history: list[JobAttempt]
