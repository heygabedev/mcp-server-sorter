import pytest
from fastapi.testclient import TestClient

from mcp_sorter.api import create_app
from mcp_sorter.collection_drift import ApplyPreview, apply_preview, preview_drift
from mcp_sorter.selections import (
    CollectionConflict,
    CollectionEdit,
    edit_selection,
    history,
    save_selection,
    set_deleted,
)


def test_preview_reports_changed_missing_and_deprecated_servers(runtime):
    saved = save_selection(runtime, "Tools", ["demo/github", "demo/slack", "demo/notion"])
    records = runtime.catalog.records()
    records = [
        s.model_copy(update={"version": "2-demo", "status": "deprecated"})
        if s.id == "demo/github"
        else s
        for s in records
        if s.id != "demo/slack"
    ]
    snapshot = runtime.catalog.publish(records, "2026-10-01T00:00:00Z")
    runtime.catalog.activate(snapshot)
    preview = preview_drift(runtime, saved.id)
    github, slack, notion = preview.changes
    assert github.changed_fields == ["status", "version"]
    assert github.after.status == "deprecated" and slack.after is None
    assert notion.changed_fields == []
    request = ApplyPreview(expected_revision=1, preview_token=preview.preview_token)
    with pytest.raises(ValueError, match="explicitly"):
        apply_preview(runtime, saved.id, request)
    updated = apply_preview(runtime, saved.id, request.model_copy(update={"remove_missing": True}))
    assert updated.revision == 2 and updated.selection.snapshot == snapshot
    assert [s.id for s in updated.selection.servers] == ["demo/github", "demo/notion"]
    assert history(runtime, saved.id)[-1].selection == saved
    with pytest.raises(CollectionConflict, match="preview"):
        apply_preview(runtime, saved.id, request)


def test_preview_is_bound_to_collection_revision_and_active_snapshot(runtime):
    saved = save_selection(runtime, "Tools", ["demo/github"])
    preview = preview_drift(runtime, saved.id)
    request = ApplyPreview(expected_revision=1, preview_token=preview.preview_token)
    other = save_selection(runtime, "Other", ["demo/github"])
    with pytest.raises(CollectionConflict):
        apply_preview(runtime, other.id, request)
    with pytest.raises(CollectionConflict):
        apply_preview(runtime, saved.id, request.model_copy(update={"preview_token": "0" * 64}))
    snapshot = runtime.catalog.publish(runtime.catalog.records(), "2026-10-02T00:00:00Z")
    runtime.catalog.activate(snapshot)
    with pytest.raises(CollectionConflict):
        apply_preview(runtime, saved.id, request)
    preview = preview_drift(runtime, saved.id)
    edit_selection(runtime, saved.id, CollectionEdit(expected_revision=1, name="Renamed"))
    with pytest.raises(CollectionConflict):
        apply_preview(
            runtime,
            saved.id,
            ApplyPreview(expected_revision=2, preview_token=preview.preview_token),
        )
    set_deleted(runtime, saved.id, 2, deleted=True)
    with pytest.raises(CollectionConflict, match="Restore"):
        preview_drift(runtime, saved.id)


def test_update_cannot_remove_every_server(runtime):
    saved = save_selection(runtime, "Tools", ["demo/github"])
    records = [s for s in runtime.catalog.records() if s.id != "demo/github"]
    runtime.catalog.activate(runtime.catalog.publish(records, "2026-10-02T00:00:00Z"))
    preview = preview_drift(runtime, saved.id)
    with pytest.raises(ValueError, match="empty"):
        apply_preview(
            runtime,
            saved.id,
            ApplyPreview(
                expected_revision=1, preview_token=preview.preview_token, remove_missing=True
            ),
        )
    assert history(runtime, saved.id)[0].revision == 1


def test_drift_api_requires_the_preview_token(runtime):
    saved = save_selection(runtime, "Tools", ["demo/github"])
    route = f"/api/v1/collections/{saved.id}"
    with TestClient(create_app(runtime.settings)) as client:
        preview = client.get(route + "/drift").json()
        assert (
            client.post(route + "/update-catalog", json={"expected_revision": 1}).status_code == 422
        )
        updated = client.post(
            route + "/update-catalog",
            json={"expected_revision": 1, "preview_token": preview["preview_token"]},
        )
        assert updated.status_code == 200 and updated.json()["revision"] == 2
