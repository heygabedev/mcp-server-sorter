import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from filelock import Timeout
from sqlalchemy import text
from typer.testing import CliRunner

from mcp_sorter.api import create_app
from mcp_sorter.cli import app
from mcp_sorter.jobs import JobRequest, enqueue, run_once
from mcp_sorter.ranking import RankRequest, rank
from mcp_sorter.recovery import (
    backup,
    digest,
    restore,
    service_lock,
    state_revision,
    validate_backup,
)
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import list_selections, save_selection
from mcp_sorter.settings import Settings
from mcp_sorter.versioning import Configuration, activate, active_versions


def test_configuration_is_immutable_and_activation_is_atomic(runtime):
    before = active_versions(runtime.engine)
    configuration = runtime.configurations.publish(
        Configuration(name="Titles first", name_weight=8)
    )
    assert configuration == runtime.configurations.publish(
        Configuration(name="Titles first", name_weight=8)
    )
    after_catalog = runtime.catalog.publish(runtime.catalog.records(), "2026-09-04T00:00:00Z")
    after = (after_catalog, configuration)
    save_selection(runtime, "Keep this", ["demo/github"])

    def change():
        for i in range(12):
            activate(runtime.catalog, runtime.configurations, *(before if i % 2 else after))

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(change)
        for _ in range(30):
            assert active_versions(runtime.engine) in (before, after)
        future.result()
    activate(runtime.catalog, runtime.configurations, *before)
    result = rank(runtime.catalog, RankRequest(query="github", configuration=configuration))
    assert result.configuration == configuration and result.snapshot == before[0]
    assert len(list_selections(runtime)) == 1
    assert len(runtime.configurations.versions()) == 2
    for invalid in ("bad", "0" * 64):
        with pytest.raises(ValueError):
            activate(runtime.catalog, runtime.configurations, before[0], invalid)
    assert active_versions(runtime.engine) == before
    runtime.configurations.path(configuration).write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        runtime.configurations.load(configuration)
    with runtime.engine.begin() as db:
        db.execute(text("DELETE FROM state WHERE key='active_configuration'"))
    with pytest.raises(ValueError, match="pair"):
        active_versions(runtime.engine)


def test_backup_restore_preserves_newer_collections_and_pinned_results(runtime, tmp_path):
    original = active_versions(runtime.engine)
    save_selection(runtime, "Original", ["demo/github"])
    enqueue(runtime, JobRequest(kind="evaluation", idempotency_key="evaluation"))
    assert run_once(runtime)
    path = backup(runtime)
    assert state_revision(path / "state.sqlite") == "0005"
    manifest = validate_backup(path)
    assert manifest.files and "state.sqlite" in manifest.files
    save_selection(runtime, "Added later", ["demo/slack"])
    new = runtime.catalog.publish(runtime.catalog.records()[:3], "2026-09-05T00:00:00Z")
    runtime.catalog.activate(new)
    destination = tmp_path.parent / (tmp_path.name + "-restored")
    result = restore(runtime, path, destination)
    restored = Runtime(Settings(data_dir=destination))
    try:
        assert active_versions(restored.engine) == original
        assert {s.name for s in list_selections(restored)} == {"Original", "Added later"}
        assert rank(restored.catalog, RankRequest(query="github")).results
        assert len(list((destination / "reports").glob("*.json"))) == 1
        assert result["pre_restore_backup"] != str(path)
        assert runtime.catalog.active() == new
    finally:
        restored.close()
    for invalid in (destination, runtime.settings.data_dir / "nested"):
        with pytest.raises(ValueError, match="new data directory"):
            restore(runtime, path, invalid)


def test_restore_requires_stopped_service(runtime, tmp_path):
    path = backup(runtime)
    with service_lock(runtime.settings.data_dir), pytest.raises(Timeout):
        restore(runtime, path, tmp_path.parent / "stopped-service-copy")


def test_restore_upgrades_previous_state_revision_and_recovers_running_jobs(runtime, tmp_path):
    enqueue(runtime, JobRequest(kind="evaluation", idempotency_key="interrupted"))
    with runtime.engine.begin() as db:
        db.execute(
            text("UPDATE jobs SET status='running',owner='old-worker',lease_until=9999999999")
        )
    path = backup(runtime)
    # Reconstruct the preceding migration's actual table shape in the backup fixture.
    with closing(sqlite3.connect(path / "state.sqlite")) as db, db:
        db.execute("ALTER TABLE activations DROP COLUMN configuration")
        db.execute("UPDATE alembic_version SET version_num='0004'")
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["state_revision"] = "0004"
    manifest["files"]["state.sqlite"] = digest(path / "state.sqlite")
    (path / "manifest.json").write_text(json.dumps(manifest))
    target = tmp_path.parent / (tmp_path.name + "-upgraded")
    restore(runtime, path, target)
    assert state_revision(target / "state.sqlite") == "0005"
    with closing(sqlite3.connect(target / "state.sqlite")) as db:
        assert db.execute("SELECT status,owner FROM jobs").fetchone() == ("queued", None)


def test_changed_backup_during_restore_never_publishes_target(runtime, tmp_path, monkeypatch):
    import shutil

    path = backup(runtime)
    original = shutil.copyfile

    def alter(source, destination):
        result = original(source, destination)
        if destination.parent.name.startswith(".restore-"):
            destination.write_bytes(b"changed")
        return result

    monkeypatch.setattr("mcp_sorter.recovery.shutil.copyfile", alter)
    target = tmp_path.parent / (tmp_path.name + "-changed")
    with pytest.raises(ValueError, match="changed"):
        restore(runtime, path, target)
    assert not target.exists()


def test_backup_rejects_linked_artifacts(runtime, tmp_path):
    target = tmp_path / "outside.json"
    target.write_text("{}")
    link = runtime.settings.data_dir / "reports" / ("1" * 32 + ".json")
    link.parent.mkdir(exist_ok=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks requires host permission")
    with pytest.raises(ValueError, match="link"):
        backup(runtime)


@pytest.mark.parametrize(
    "case",
    ["checksum", "missing", "path", "digest", "schema", "state", "active", "revision", "report"],
)
def test_backup_validation_rejects_unsafe_or_incomplete_bundles(runtime, case):
    enqueue(runtime, JobRequest(kind="evaluation", idempotency_key="report"))
    run_once(runtime)
    path = backup(runtime)
    manifest_path = path / "manifest.json"
    data = json.loads(manifest_path.read_text())
    first = next(name for name in data["files"] if name.startswith("catalogs/"))
    if case == "checksum":
        (path / first).write_bytes(b"corrupt")
    elif case == "missing":
        (path / first).unlink()
    elif case == "path":
        data["files"]["../escape"] = "0" * 64
    elif case == "digest":
        data["files"][first] = "invalid"
    elif case == "schema":
        data["state_revision"] = "future"
    elif case == "state":
        del data["files"]["state.sqlite"]
    elif case == "active":
        del data["files"][first]
    elif case == "revision":
        data["state_revision"] = "0004"
    elif case == "report":
        data["files"] = {
            name: value for name, value in data["files"].items() if not name.startswith("reports/")
        }
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        validate_backup(path)


def test_rejects_missing_manifest_future_state_and_poisoned_search(runtime, tmp_path):
    with pytest.raises(ValueError, match="manifest"):
        validate_backup(tmp_path)
    path = backup(runtime)
    with closing(sqlite3.connect(path / "state.sqlite")) as db, db:
        db.execute("UPDATE state SET value='2' WHERE key='schema_version'")
    with pytest.raises(ValueError, match="schema"):
        state_revision(path / "state.sqlite")
    with closing(sqlite3.connect(runtime.catalog.path(runtime.catalog.active()))) as db, db:
        db.execute("UPDATE search SET name='poisoned' WHERE id='demo/github'")
    with pytest.raises(ValueError, match="index"):
        runtime.catalog.validate(runtime.catalog.active())


def test_backup_api_versions_and_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("SORTER_DATA_DIR", str(tmp_path))
    with TestClient(create_app(Settings(data_dir=tmp_path))) as http:
        version = http.get("/api/v1/versions").json()
        configuration = http.post(
            "/api/v1/configurations", json={"name": "Alternative", "name_weight": 8}
        ).json()["id"]
        assert (
            http.post(
                "/api/v1/versions/activate",
                json={"snapshot": version["active_catalog"], "configuration": configuration},
            ).status_code
            == 200
        )
        assert (
            http.post(
                "/api/v1/versions/activate",
                json={"snapshot": "0" * 64, "configuration": configuration},
            ).status_code
            == 422
        )
        saved = http.post("/api/v1/backups").json()["id"]
        assert saved in http.get("/api/v1/backups").json()
        assert http.get(f"/api/v1/backups/{saved}").status_code == 200
        assert http.get("/api/v1/backups/invalid").status_code == 422
    runner = CliRunner()
    created = runner.invoke(app, ["backup", "create"])
    assert created.exit_code == 0, created.output
    assert runner.invoke(app, ["backup", "verify", created.stdout.strip()]).exit_code == 0
    assert runner.invoke(app, ["config", "create", "CLI config"]).exit_code == 0
    assert runner.invoke(app, ["activate", version["active_catalog"], configuration]).exit_code == 0
    destination = tmp_path.parent / (tmp_path.name + "-cli-copy")
    result = runner.invoke(app, ["backup", "restore", created.stdout.strip(), str(destination)])
    assert result.exit_code == 0, result.output
    assert digest(destination / "state.sqlite")
