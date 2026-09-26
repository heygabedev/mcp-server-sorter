import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_sorter import __version__
from mcp_sorter.catalog import Catalog
from mcp_sorter.models import Filters, ServerRecord

POLICY_VERSION = "bm25-evidence-v1"
Profile = Literal[
    "baseline",
    "demo-balanced",
    "demo-fast",
    "demo-timeout",
    "demo-malformed",
    "demo-rate-limited",
    "demo-unavailable",
    "openrouter",
    "litellm",
]


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=500)
    filters: Filters = Field(default_factory=Filters)
    snapshot: str | None = None
    limit: int = Field(default=20, ge=1, le=50)
    profile: Profile = "baseline"


class RankedServer(BaseModel):
    server: ServerRecord
    relevance: float
    reasons: list[str]
    evidence_ids: list[str]


class Ranking(BaseModel):
    schema_version: int = 1
    application_version: str = __version__
    snapshot: str
    as_of: str
    policy_version: str = POLICY_VERSION
    sqlite_version: str = sqlite3.sqlite_version
    mode: str = "deterministic"
    query: str
    filters: Filters
    results: list[RankedServer]
    fallback_reason: str | None = None
    model_metadata: dict[str, Any] = Field(default_factory=dict)


def query_expression(query: str) -> str:
    tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold())))[:32]
    return " OR ".join(f'"{token}"' for token in tokens)


def rank(catalog: Catalog, request: RankRequest) -> Ranking:
    snapshot = request.snapshot or catalog.active()
    expression = query_expression(request.query)
    with catalog.connect(snapshot) as db:
        manifest = json.loads(db.execute("SELECT payload FROM manifest").fetchone()[0])
        if request.query.strip() and not expression:
            rows = []
        elif expression:
            rows = db.execute(
                "SELECT servers.payload, bm25(search,0,5,1,2) FROM search "
                "JOIN servers ON servers.id=search.id WHERE search MATCH ?",
                (expression,),
            ).fetchall()
        else:
            rows = db.execute("SELECT payload, 0.0 FROM servers ORDER BY id").fetchall()
    as_of = datetime.fromisoformat(manifest["as_of"])
    candidates: list[tuple[float, int, float, str, ServerRecord]] = []
    for payload, score in rows:
        server = ServerRecord.model_validate_json(payload)
        if request.filters.matches(server):
            freshness = min(server.updated_at.timestamp(), as_of.timestamp())
            candidates.append((score, -len(server.evidence), -freshness, server.id, server))
    candidates.sort(key=lambda item: item[:4])
    results = [
        RankedServer(
            server=server,
            relevance=-float(score),
            reasons=[
                "Matches the search terms" if expression else "Included in this catalog",
                f"{len(server.evidence)} supporting evidence item(s)",
            ],
            evidence_ids=[item.id for item in server.evidence],
        )
        for score, _, _, _, server in candidates[: request.limit]
    ]
    return Ranking(
        snapshot=snapshot,
        as_of=manifest["as_of"],
        query=request.query,
        filters=request.filters,
        results=results,
    )


def compare(catalog: Catalog, ids: list[str], snapshot: str | None = None) -> list[ServerRecord]:
    if not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
        raise ValueError("Choose between two and four distinct servers")
    records = {server.id: server for server in catalog.records(snapshot)}
    if any(identifier not in records for identifier in ids):
        raise ValueError("A selected server is missing from this snapshot")
    return [records[identifier] for identifier in ids]
