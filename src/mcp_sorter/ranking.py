import re
import sqlite3
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_sorter import __version__
from mcp_sorter.catalog import Catalog
from mcp_sorter.models import Filters, ServerRecord
from mcp_sorter.versioning import Configurations, active_versions

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
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(default="", max_length=500)
    filters: Filters = Field(default_factory=Filters)
    snapshot: str | None = None
    configuration: str | None = None
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
    configuration: str = ""
    prompt_version: str = "evidence-order-v1"
    model_profiles_version: str = "profiles-v1"
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
    active_catalog, active_configuration = active_versions(catalog.engine)
    snapshot = request.snapshot or active_catalog
    configuration = request.configuration or active_configuration
    policy = Configurations(catalog.directory.parent, catalog.engine).load(configuration)
    expression = query_expression(request.query)
    with catalog.connect(snapshot) as db:
        as_of = str(
            db.execute("SELECT json_extract(payload,'$.as_of') FROM manifest").fetchone()[0]
        )
        timestamp = datetime.fromisoformat(as_of).timestamp()
        db.create_function(
            "freshness",
            1,
            lambda value: min(datetime.fromisoformat(value).timestamp(), timestamp),
            deterministic=True,
        )
        conditions = []
        parameters: list[object] = []
        # Only fixed field names enter SQL; every user-provided value is bound.
        for field in ("category", "transport", "auth", "deployment", "license"):
            value = getattr(request.filters, field)
            if value is not None:
                conditions.append(f"json_extract(servers.payload,'$.{field}') = ?")
                parameters.append(value)
        if not request.filters.include_deprecated:
            conditions.append("json_extract(servers.payload,'$.status') != 'deprecated'")
        score = "0.0"
        source = "servers"
        if expression:
            score = "bm25(search,0,?,?,?)"
            parameters = [
                policy.name_weight,
                policy.description_weight,
                policy.tags_weight,
                expression,
                *parameters,
            ]
            source = "search JOIN servers ON servers.id=search.id"
            conditions.insert(0, "search MATCH ?")
        where = " AND ".join(conditions) or "1"
        if request.query.strip() and not expression:
            rows = []
        else:
            rows = db.execute(
                f"SELECT servers.payload, {score} AS score FROM {source} WHERE {where} "
                "ORDER BY score, "
                "json_array_length(json_extract(servers.payload,'$.evidence')) DESC, "
                "freshness(json_extract(servers.payload,'$.updated_at')) DESC, servers.id LIMIT ?",
                (*parameters, request.limit),
            ).fetchall()
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
        for payload, score in rows
        for server in [ServerRecord.model_validate_json(payload)]
    ]
    return Ranking(
        snapshot=snapshot,
        configuration=configuration,
        as_of=as_of,
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
