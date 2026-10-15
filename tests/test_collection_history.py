import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from filelock import Timeout
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mcp_sorter.api import create_app
from mcp_sorter.recovery import service_lock, state_revision, validate_backup
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import (
    export_selection,
    history,
    import_selection,
    list_selections,
    save_selection,
)


def legacy_state(runtime):
    runtime.close()
    with closing(sqlite3.connect(runtime.settings.data_dir / "state.sqlite")) as db, db:
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
        assert state_revision(runtime.settings.data_dir / "state.sqlite") == "0006"
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
