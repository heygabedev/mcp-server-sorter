import hashlib
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from mcp_sorter.models import ServerRecord
from mcp_sorter.runtime import Runtime
from mcp_sorter.storage import canonical


class Selection(BaseModel):
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


class SelectionExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection: Selection


def save_selection(
    runtime: Runtime, name: str, ids: list[str], snapshot: str | None = None
) -> Selection:
    snapshot = snapshot or runtime.catalog.active()
    records = {record.id: record for record in runtime.catalog.records(snapshot)}
    if any(identifier not in records for identifier in ids):
        raise ValueError("A selected server is missing from this snapshot")
    selection = Selection(
        id=uuid4().hex,
        name=name,
        snapshot=snapshot,
        created_at=datetime.now(UTC),
        servers=[records[identifier] for identifier in ids],
    )
    _insert(runtime, selection)
    return selection


def _insert(runtime: Runtime, selection: Selection) -> None:
    with runtime.engine.begin() as db:
        db.execute(
            text("INSERT INTO collections VALUES (:id,:payload)"),
            {"id": selection.id, "payload": selection.model_dump_json()},
        )


def list_selections(runtime: Runtime) -> list[Selection]:
    with runtime.engine.connect() as db:
        return [
            Selection.model_validate_json(row[0])
            for row in db.execute(text("SELECT payload FROM collections ORDER BY id"))
        ]


def get_selection(runtime: Runtime, identifier: str) -> Selection:
    with runtime.engine.connect() as db:
        payload = db.execute(
            text("SELECT payload FROM collections WHERE id=:id"), {"id": identifier}
        ).scalar_one_or_none()
    if payload is None:
        raise ValueError("Collection not found")
    return Selection.model_validate_json(payload)


def export_selection(runtime: Runtime, identifier: str) -> SelectionExport:
    selection = get_selection(runtime, identifier)
    digest = hashlib.sha256(canonical(selection.model_dump(mode="json")).encode()).hexdigest()
    return SelectionExport(content_sha256=digest, selection=selection)


def import_selection(runtime: Runtime, bundle: SelectionExport) -> Selection:
    digest = hashlib.sha256(
        canonical(bundle.selection.model_dump(mode="json")).encode()
    ).hexdigest()
    if digest != bundle.content_sha256:
        raise ValueError("Selection checksum mismatch")
    selection = bundle.selection.model_copy(update={"id": uuid4().hex})
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
