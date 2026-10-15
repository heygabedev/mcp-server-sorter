import hashlib
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from mcp_sorter.models import ServerRecord, VersionedModel
from mcp_sorter.runtime import Runtime
from mcp_sorter.storage import canonical


class Selection(VersionedModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=80)
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: AwareDatetime
    servers: list[ServerRecord] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def distinct_servers(self) -> Self:
        if len({server.id for server in self.servers}) != len(self.servers):
            raise ValueError("A selection cannot contain duplicate server IDs")
        return self


class SelectionExport(VersionedModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection: Selection


class CollectionRevision(BaseModel):
    revision: int = Field(ge=1)
    deleted: bool
    changed_at: AwareDatetime
    selection: Selection


def save_selection(
    runtime: Runtime, name: str, ids: list[str], snapshot: str | None = None
) -> Selection:
    snapshot = snapshot or runtime.catalog.active()
    records = runtime.catalog.records_by_ids(ids, snapshot)
    selection = Selection(
        id=uuid4().hex,
        name=name,
        snapshot=snapshot,
        created_at=datetime.now(UTC),
        servers=records,
    )
    _insert(runtime, selection)
    return selection


def _insert(runtime: Runtime, selection: Selection) -> None:
    with runtime.engine.begin() as db:
        db.execute(
            text("INSERT INTO collections(id,payload) VALUES (:id,:payload)"),
            {"id": selection.id, "payload": selection.model_dump_json()},
        )
        db.execute(
            text("INSERT INTO collection_revisions VALUES (:id,1,:payload,0,:at)"),
            {
                "id": selection.id,
                "payload": selection.model_dump_json(),
                "at": selection.created_at.isoformat(),
            },
        )


def list_selections(runtime: Runtime) -> list[Selection]:
    with runtime.engine.connect() as db:
        return [
            Selection.model_validate_json(row[0])
            for row in db.execute(
                text(
                    "SELECT payload FROM collections WHERE deleted=0 "
                    "ORDER BY julianday(json_extract(payload,'$.created_at')) DESC,id DESC"
                )
            )
        ]


def get_selection(runtime: Runtime, identifier: str) -> Selection:
    with runtime.engine.connect() as db:
        payload = db.execute(
            text("SELECT payload FROM collections WHERE id=:id"), {"id": identifier}
        ).scalar_one_or_none()
    if payload is None:
        raise ValueError("Collection not found")
    return Selection.model_validate_json(payload)


def history(runtime: Runtime, identifier: str) -> list[CollectionRevision]:
    with runtime.engine.connect() as db:
        rows = db.execute(
            text(
                "SELECT revision,deleted,changed_at,payload FROM collection_revisions "
                "WHERE collection_id=:id ORDER BY revision DESC"
            ),
            {"id": identifier},
        ).all()
    if not rows:
        raise ValueError("Collection not found")
    return [
        CollectionRevision(
            revision=r[0],
            deleted=r[1],
            changed_at=r[2],
            selection=Selection.model_validate_json(r[3]),
        )
        for r in rows
    ]


def export_selection(
    runtime: Runtime, identifier: str, revision: int | None = None
) -> SelectionExport:
    if revision is None:
        selection = get_selection(runtime, identifier)
    else:
        item = next(
            (item for item in history(runtime, identifier) if item.revision == revision), None
        )
        if item is None:
            raise ValueError("Collection revision not found")
        selection = item.selection
    digest = hashlib.sha256(canonical(selection.model_dump(mode="json")).encode()).hexdigest()
    return SelectionExport(content_sha256=digest, selection=selection)


def import_selection(runtime: Runtime, bundle: SelectionExport) -> Selection:
    digest = hashlib.sha256(
        canonical(bundle.selection.model_dump(mode="json")).encode()
    ).hexdigest()
    if digest != bundle.content_sha256:
        raise ValueError("Selection checksum mismatch")
    selection = bundle.selection.model_copy(
        update={"id": uuid4().hex, "created_at": datetime.now(UTC)}
    )
    _insert(runtime, selection)
    return selection


def catalog_diff(runtime: Runtime, before: str, after: str) -> dict[str, list[str]]:
    old = {s.id: s for s in runtime.catalog.records(before)}
    new = {s.id: s for s in runtime.catalog.records(after)}
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
        "deprecated": sorted(
            key
            for key in old.keys() & new.keys()
            if new[key].status == "deprecated" and old[key].status != "deprecated"
        ),
    }
