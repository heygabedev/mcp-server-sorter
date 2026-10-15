import base64
import hashlib
import hmac
import re
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text

from mcp_sorter.catalog import Catalog
from mcp_sorter.ranking import Ranking, RankRequest, retrieve
from mcp_sorter.storage import canonical, get_value


class CatalogSearch(RankRequest):
    profile: Literal["baseline"] = "baseline"
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class CatalogPage(Ranking):
    total: int
    next_cursor: str | None = None


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    position: tuple[float, int, float, str]


def cursor_key(engine: Engine) -> bytes:
    value = get_value(engine, "cursor_key")
    if value is None:
        with engine.begin() as db:
            db.execute(
                text("INSERT OR IGNORE INTO state(key,value) VALUES ('cursor_key',:value)"),
                {"value": secrets.token_hex(32)},
            )
            value = str(
                db.execute(text("SELECT value FROM state WHERE key='cursor_key'")).scalar_one()
            )
    return bytes.fromhex(value)


def fingerprint(request: RankRequest) -> str:
    payload = request.model_dump(include={"query", "filters", "limit", "snapshot", "configuration"})
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def decode_cursor(value: str, key: bytes) -> Cursor:
    encoded, _, signature = value.partition(".")
    expected = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    if not re.fullmatch(r"[a-f0-9]{64}", signature) or not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid continuation cursor")
    try:
        return Cursor.model_validate_json(base64.b64decode(encoded, altchars=b"-_", validate=True))
    except ValueError as exc:
        raise ValueError("Invalid continuation cursor") from exc


def encode_cursor(cursor: Cursor, key: bytes) -> str:
    encoded = base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode()
    signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def search_catalog(catalog: Catalog, request: CatalogSearch) -> CatalogPage:
    key = cursor_key(catalog.engine)
    cursor = decode_cursor(request.cursor, key) if request.cursor else None
    if cursor:
        if (request.snapshot and request.snapshot != cursor.snapshot) or (
            request.configuration and request.configuration != cursor.configuration
        ):
            raise ValueError("Cursor belongs to different version pins")
        request = request.model_copy(
            update={"snapshot": cursor.snapshot, "configuration": cursor.configuration}
        )
        if fingerprint(request) != cursor.request_sha256:
            raise ValueError("Cursor belongs to a different query, filters or page size")
    ranking, total = retrieve(
        catalog, request, paginate=True, position=cursor.position if cursor else None
    )
    next_cursor = None
    if len(ranking.results) > request.limit:
        ranking.results = ranking.results[: request.limit]
        last = ranking.results[-1].retrieval
        assert last is not None
        pinned = request.model_copy(
            update={"snapshot": ranking.snapshot, "configuration": ranking.configuration}
        )
        next_cursor = encode_cursor(
            Cursor(
                snapshot=ranking.snapshot,
                configuration=ranking.configuration,
                request_sha256=fingerprint(pinned),
                position=(
                    last.bm25_score,
                    last.evidence_count,
                    last.freshness_at.timestamp(),
                    last.stable_id,
                ),
            ),
            key,
        )
    return CatalogPage(**ranking.model_dump(), total=total, next_cursor=next_cursor)
