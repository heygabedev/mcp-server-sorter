import re
import sqlite3
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mcp_sorter import __version__
from mcp_sorter.catalog import Catalog
from mcp_sorter.models import Filters, ServerRecord
from mcp_sorter.versioning import Configurations, active_versions

POLICY_VERSION = "bm25-evidence-v1"
MatchField = Literal["name", "description", "tags"]
SEARCH_FIELDS: tuple[MatchField, ...] = ("name", "description", "tags")
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


class RetrievalExplanation(BaseModel):
    version: Literal["retrieval-evidence-v1"] = "retrieval-evidence-v1"
    matched_fields: list[MatchField]
    applied_filters: Filters
    bm25_score: float = Field(allow_inf_nan=False)
    evidence_ids: list[str]
    evidence_count: int = Field(ge=0)
    freshness_at: AwareDatetime
    stable_id: str


class RankedServer(BaseModel):
    server: ServerRecord
    relevance: float
    reasons: list[str]
    evidence_ids: list[str]
    retrieval: RetrievalExplanation | None = None


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
    return retrieve(catalog, request)[0]


def retrieve(
    catalog: Catalog,
    request: RankRequest,
    *,
    paginate: bool = False,
    position: tuple[float, int, float, str] | None = None,
) -> tuple[Ranking, int]:
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
        matches = ",".join(f"0 AS matched_{field}" for field in SEARCH_FIELDS)
        source = "servers"
        if expression:
            score = "bm25(search,0,?,?,?)"
            matches = ",".join(
                f"highlight(search,{index},'<','>') != search.{field} AS matched_{field}"
                for index, field in enumerate(SEARCH_FIELDS, 1)
            )
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
        total = 0
        if request.query.strip() and not expression:
            rows = []
        elif paginate:
            total = db.execute(
                f"SELECT COUNT(*) FROM {source} WHERE {where}",
                parameters[3:] if expression else parameters,
            ).fetchone()[0]
            continuation = ""
            after: tuple[object, ...] = ()
            if position is not None:
                score_at, evidence_at, freshness_at, identifier = position
                continuation = (
                    "WHERE (score,-evidence_count,-freshness_used,server_id) > (?,?,?,?) "
                )
                after = (score_at, -evidence_at, -freshness_at, identifier)
            rows = db.execute(
                "WITH ranked AS MATERIALIZED ("
                f"SELECT servers.payload AS payload, {score} AS score, {matches}, "
                "json_array_length(json_extract(servers.payload,'$.evidence')) AS evidence_count, "
                "freshness(json_extract(servers.payload,'$.updated_at')) AS freshness_used, "
                f"servers.id AS server_id FROM {source} WHERE {where}) "
                "SELECT payload,score,matched_name,matched_description,matched_tags FROM ranked "
                + continuation
                + "ORDER BY score,evidence_count DESC,freshness_used DESC,server_id LIMIT ?",
                (*parameters, *after, request.limit + 1),
            ).fetchall()
        else:
            rows = db.execute(
                f"SELECT servers.payload, {score} AS score, {matches} FROM {source} WHERE {where} "
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
            retrieval=RetrievalExplanation(
                matched_fields=[
                    field for field, matched in zip(SEARCH_FIELDS, flags, strict=True) if matched
                ],
                applied_filters=request.filters,
                bm25_score=float(score),
                evidence_ids=[item.id for item in server.evidence],
                evidence_count=len(server.evidence),
                freshness_at=min(server.updated_at, datetime.fromisoformat(as_of)),
                stable_id=server.id,
            ),
        )
        for payload, score, *flags in rows
        for server in [ServerRecord.model_validate_json(payload)]
    ]
    result = Ranking(
        snapshot=snapshot,
        configuration=configuration,
        as_of=as_of,
        query=request.query,
        filters=request.filters,
        results=results,
    )
    return result, total


def compare(catalog: Catalog, ids: list[str], snapshot: str | None = None) -> list[ServerRecord]:
    if not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
        raise ValueError("Choose between two and four distinct servers")
    records = {server.id: server for server in catalog.records(snapshot)}
    if any(identifier not in records for identifier in ids):
        raise ValueError("A selected server is missing from this snapshot")
    return [records[identifier] for identifier in ids]
