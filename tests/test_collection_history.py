import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from filelock import Timeout
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mcp_sorter.api import create_app
from mcp_sorter.recovery import service_lock, state_revision, validate_backup
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import (
    CollectionConflict,
    CollectionEdit,
    current_revision,
    deleted_selections,
    edit_selection,
    export_selection,
    history,
    import_selection,
    list_selections,
    save_selection,
    set_deleted,
)


def legacy_state(runtime):
    runtime.close()
    with closing(sqlite3.connect(runtime.settings.data_dir / "state.sqlite")) as db, db:
        db.execute("DROP TABLE job_attempts")
        db.execute("ALTER TABLE jobs DROP COLUMN parent_job_id")
        db.execute("ALTER TABLE jobs DROP COLUMN request_trace_id")
        db.execute("ALTER TABLE jobs DROP COLUMN request_span_id")
        db.execute("DROP TABLE collection_revisions")
        db.execute("ALTER TABLE collections DROP COLUMN current_revision")
        db.execute("ALTER TABLE collections DROP COLUMN deleted")
        db.execute("UPDATE alembic_version SET version_num='0005'")


def test_migration_keeps_existing_ids_and_records_with_a_verified_backup(runtime):
    saved = save_selection(runtime, "Original", ["demo/github"])
    legacy_state(runtime)
    upgraded = Runtime(runtime.settings)
    try:
        revisions = history(upgraded, saved.id)
        assert len(revisions) == 1 and revisions[0].revision == 1
        assert revisions[0].selection == saved and not revisions[0].deleted
        backups = list((runtime.settings.data_dir / "backups").iterdir())
        assert len(backups) == 1
        assert validate_backup(backups[0]).state_revision == "0005"
        assert state_revision(runtime.settings.data_dir / "state.sqlite") == "0007"
        with pytest.raises(IntegrityError, match="immutable"), upgraded.engine.begin() as db:
            db.execute(text("UPDATE collection_revisions SET deleted=1"))
        with pytest.raises(IntegrityError, match="immutable"), upgraded.engine.begin() as db:
            db.execute(text("DELETE FROM collection_revisions"))
    finally:
        upgraded.close()
    reopened = Runtime(runtime.settings)
    reopened.close()
    assert len(list((runtime.settings.data_dir / "backups").iterdir())) == 1


def test_migration_does_not_change_state_if_backup_verification_fails(runtime):
    path = runtime.catalog.path(runtime.catalog.active())
    legacy_state(runtime)
    path.unlink()
    with pytest.raises(ValueError, match="missing an active artifact"):
        Runtime(runtime.settings)
    assert state_revision(runtime.settings.data_dir / "state.sqlite") == "0005"


def test_migration_rejects_a_running_service(runtime):
    legacy_state(runtime)
    with service_lock(runtime.settings.data_dir), pytest.raises(Timeout):
        Runtime(runtime.settings)
    assert state_revision(runtime.settings.data_dir / "state.sqlite") == "0005"


def test_application_startup_upgrades_under_its_existing_service_lock(runtime):
    saved = save_selection(runtime, "Startup", ["demo/github"])
    legacy_state(runtime)
    with TestClient(create_app(runtime.settings)) as client:
        assert client.get("/api/v1/collections").json()[0]["id"] == saved.id
        assert client.get("/health/ready").status_code == 200


def test_new_imported_collections_start_at_revision_one_and_sort_newest_first(runtime):
    first = save_selection(runtime, "First", ["demo/github"])
    second = save_selection(runtime, "Second", ["demo/slack"])
    imported = import_selection(runtime, export_selection(runtime, first.id, revision=1))
    assert [s.id for s in list_selections(runtime)] == [imported.id, second.id, first.id]
    assert history(runtime, imported.id)[0].selection.servers == first.servers
    assert history(runtime, imported.id)[0].revision == 1
    with pytest.raises(ValueError, match="revision not found"):
        export_selection(runtime, first.id, revision=2)
    with pytest.raises(ValueError, match="not found"):
        history(runtime, "missing")


def test_edits_deletion_and_restoration_keep_all_revisions(runtime):
    saved = save_selection(runtime, "First", ["demo/github"])
    changed = edit_selection(
        runtime,
        saved.id,
        CollectionEdit(expected_revision=1, name="Renamed", ids=["demo/slack", "demo/github"]),
    )
    assert changed.revision == 2 and changed.selection.name == "Renamed"
    assert [s.id for s in changed.selection.servers] == ["demo/slack", "demo/github"]
    assert export_selection(runtime, saved.id, 1).selection == saved
    assert export_selection(runtime, saved.id).selection == changed.selection
    deleted = set_deleted(runtime, saved.id, 2, deleted=True)
    assert deleted.deleted and deleted.revision == 3 and list_selections(runtime) == []
    assert deleted_selections(runtime) == [deleted]
    with pytest.raises(CollectionConflict, match="Restore"):
        edit_selection(runtime, saved.id, CollectionEdit(expected_revision=3, name="Too soon"))
    with pytest.raises(CollectionConflict, match="already"):
        set_deleted(runtime, saved.id, 3, deleted=True)
    restored = set_deleted(runtime, saved.id, 3, deleted=False)
    assert restored.revision == 4 and not restored.deleted
    assert list_selections(runtime) == [changed.selection]
    assert [r.revision for r in history(runtime, saved.id)] == [4, 3, 2, 1]
    for revision in (1, 5):
        with pytest.raises(CollectionConflict):
            edit_selection(
                runtime, saved.id, CollectionEdit(expected_revision=revision, name="Stale")
            )
        with pytest.raises(CollectionConflict):
            set_deleted(runtime, saved.id, revision, deleted=True)


def test_concurrent_edits_allow_only_one_expected_revision(runtime, monkeypatch):
    saved = save_selection(runtime, "Original", ["demo/github"])
    barrier = Barrier(2)

    def read_together(*args):
        value = current_revision(*args)
        barrier.wait(timeout=10)
        return value

    monkeypatch.setattr("mcp_sorter.selections.current_revision", read_together)

    def edit(name):
        try:
            return edit_selection(runtime, saved.id, CollectionEdit(expected_revision=1, name=name))
        except CollectionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(edit, ["First editor", "Second editor"]))
    assert sum(value is not None for value in results) == 1
    assert len(history(runtime, saved.id)) == 2


def test_edit_validation_and_imported_record_removal_without_source_catalog(runtime):
    for changes in [{}, {"ids": []}, {"ids": ["demo/github"] * 2}, {"name": ""}]:
        with pytest.raises(ValueError):
            CollectionEdit(expected_revision=1, **changes)
    saved = save_selection(runtime, "Portable", ["demo/github", "demo/slack"])
    runtime.catalog.path(saved.snapshot).unlink()
    edited = edit_selection(
        runtime, saved.id, CollectionEdit(expected_revision=1, ids=["demo/github"])
    )
    assert edited.selection.servers == saved.servers[:1]
    with pytest.raises(ValueError, match="Unknown catalog"):
        edit_selection(runtime, saved.id, CollectionEdit(expected_revision=2, ids=["demo/gitlab"]))


def test_revision_api_edits_and_history_exports(runtime):
    with TestClient(create_app(runtime.settings)) as client:
        saved = client.post(
            "/api/v1/collections", json={"name": "API", "ids": ["demo/github"]}
        ).json()
        route = f"/api/v1/collections/{saved['id']}"
        assert client.get(route).json()["revision"] == 1
        assert (
            client.post(
                route + "/edit", json={"expected_revision": 1, "name": "Updated"}
            ).status_code
            == 200
        )
        assert (
            client.post(route + "/edit", json={"expected_revision": 1, "name": "Stale"}).status_code
            == 409
        )
        assert (
            client.post(
                route + "/edit", json={"expected_revision": 2, "ids": ["missing"]}
            ).status_code
            == 422
        )
        assert client.get(route + "/export?revision=1").json()["selection"]["name"] == "API"
        assert client.get(route + "/export").json()["selection"]["name"] == "Updated"
        assert client.get(route + "/export?revision=99").status_code == 404
        assert client.post(route + "/delete", json={"expected_revision": 2}).status_code == 200
        assert client.get("/api/v1/collections").json() == []
        assert len(client.get("/api/v1/collections/deleted").json()) == 1
        assert client.post(route + "/restore", json={"expected_revision": 3}).status_code == 200
        assert len(client.get(route + "/history").json()) == 4
        assert client.get("/api/v1/collections/missing").status_code == 404
        assert client.get("/api/v1/collections/" + "0" * 32).status_code == 404
        unsupported = client.get("/api/v1/collections/import")
        assert unsupported.status_code == 405
        assert unsupported.headers["allow"] == "POST"
        assert client.post("/api/v1/collections/deleted", json={}).status_code == 405
