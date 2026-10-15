import hashlib
import hmac

from pydantic import BaseModel, Field

from mcp_sorter.models import ServerRecord
from mcp_sorter.runtime import Runtime
from mcp_sorter.search import cursor_key
from mcp_sorter.selections import (
    CollectionConflict,
    CollectionRevision,
    RevisionRequest,
    _write_revision,
    current_revision,
)
from mcp_sorter.storage import canonical


class ServerChange(BaseModel):
    before: ServerRecord
    after: ServerRecord | None
    changed_fields: list[str]


class DriftPreview(BaseModel):
    collection_id: str
    revision: int
    snapshot: str
    changes: list[ServerChange]
    preview_token: str


class ApplyPreview(RevisionRequest):
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    remove_missing: bool = False


def preview_drift(runtime: Runtime, identifier: str) -> DriftPreview:
    current = current_revision(runtime, identifier)
    if current.deleted:
        raise CollectionConflict("Restore this collection before updating its catalog")
    snapshot = runtime.catalog.active()
    records = {
        s.id: s
        for s in runtime.catalog.records_by_ids(
            [s.id for s in current.selection.servers], snapshot, missing_ok=True
        )
    }
    changes = [
        ServerChange(
            before=old,
            after=records.get(old.id),
            changed_fields=sorted(
                key
                for key, value in old.model_dump().items()
                if old.id in records and value != records[old.id].model_dump()[key]
            ),
        )
        for old in current.selection.servers
    ]
    preview = DriftPreview(
        collection_id=identifier,
        revision=current.revision,
        snapshot=snapshot,
        changes=changes,
        preview_token="",
    )
    # Domain separation keeps collection approvals distinct from search continuations.
    content = b"collection-drift-v1:" + canonical(preview.model_dump(mode="json")).encode()
    preview.preview_token = hmac.new(
        cursor_key(runtime.engine), content, hashlib.sha256
    ).hexdigest()
    return preview


def apply_preview(runtime: Runtime, identifier: str, request: ApplyPreview) -> CollectionRevision:
    preview = preview_drift(runtime, identifier)
    if preview.revision != request.expected_revision or not hmac.compare_digest(
        preview.preview_token, request.preview_token
    ):
        raise CollectionConflict("Collection or catalog changed; preview the update again")
    if any(change.after is None for change in preview.changes) and not request.remove_missing:
        raise ValueError("Missing servers must be removed explicitly or the update cancelled")
    records = [change.after for change in preview.changes if change.after is not None]
    if not records:
        raise ValueError("Updating would leave an empty collection")
    current = current_revision(runtime, identifier)
    selection = current.selection.model_copy(
        update={"snapshot": preview.snapshot, "servers": records}
    )
    return _write_revision(runtime, selection, request.expected_revision)
