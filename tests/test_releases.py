import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from mcp_sorter.cli import app
from mcp_sorter.recovery import digest, state_revision, validate_backup
from mcp_sorter.releases import (
    activate_release,
    directory,
    installed,
    pointer,
    python_path,
    rollback_release,
    serve_release,
    stage,
)
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings


def wheel(tmp_path, version="0.1.0", revisions=None, interface=True):
    path = tmp_path / f"mcp_server_sorter-{version}-py3-none-any.whl"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "mcp_sorter/release.json",
            json.dumps(
                {
                    "format_version": 1,
                    "application_version": version,
                    "python": "3.13",
                    "readable_state_revisions": revisions or ["0006"],
                }
            ),
        )
        if interface:
            archive.writestr("mcp_sorter/static/index.html", "<title>Sorter</title>")
    return path


def fake_installer(monkeypatch, settings, path):
    executable = python_path(directory(settings, digest(path)))
    executable.parent.mkdir(parents=True)
    executable.touch()
    version = path.name.split("-")[1]
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=version)

    monkeypatch.setattr("mcp_sorter.releases.subprocess.run", run)
    return calls


def test_pinned_wheels_activate_and_roll_back_without_mutating_data(runtime, tmp_path, monkeypatch):
    settings = runtime.settings
    rc = wheel(tmp_path, "0.1.0rc1")
    fake_installer(monkeypatch, settings, rc)
    record = stage(settings, rc, digest(rc), tmp_path)
    assert stage(settings, rc, digest(rc), tmp_path) == record
    activate_release(settings, record.sha256)
    final = wheel(tmp_path)
    calls = fake_installer(monkeypatch, settings, final)
    final_record = stage(settings, final, digest(final), tmp_path)
    assert any("--no-index" in call for call in calls)
    assert activate_release(settings, final_record.sha256).previous == record.sha256
    assert activate_release(settings, final_record.sha256).previous == record.sha256
    assert rollback_release(settings).active == record.sha256
    assert pointer(settings).backup is not None
    serve_release(settings, 8765)
    assert calls[-1][-1] == "8765"
    assert "serve" in calls[-1]
    monkeypatch.setenv("SORTER_DATA_DIR", str(settings.data_dir))
    runner = CliRunner()
    assert (
        runner.invoke(app, ["release", "stage", str(final), digest(final), str(tmp_path)]).exit_code
        == 0
    )
    assert runner.invoke(app, ["release", "activate", digest(final)]).exit_code == 0
    assert runner.invoke(app, ["release", "rollback"]).exit_code == 0
    assert runner.invoke(app, ["release", "serve"]).exit_code == 0


def test_incompatible_missing_and_changed_releases_are_rejected(runtime, tmp_path, monkeypatch):
    settings = runtime.settings
    for operation in (rollback_release, lambda config: serve_release(config, 8000)):
        with pytest.raises(ValueError):
            operation(settings)
    with pytest.raises(ValueError, match="digest"):
        directory(settings, "../outside")
    path = wheel(tmp_path, revisions=["0004"])
    fake_installer(monkeypatch, settings, path)
    record = stage(settings, path, digest(path), tmp_path)
    with pytest.raises(ValueError, match="schema"):
        activate_release(settings, record.sha256)
    assert pointer(settings) is None
    python_path(directory(settings, record.sha256)).unlink()
    with pytest.raises(ValueError, match="environment"):
        installed(settings, record.sha256)
    (directory(settings, record.sha256) / record.filename).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        installed(settings, record.sha256)


def test_staging_checks_artifact_and_installed_version(runtime, tmp_path, monkeypatch):
    path = wheel(tmp_path, interface=False)
    with pytest.raises(ValueError, match="pinned"):
        stage(runtime.settings, path, "0" * 64, tmp_path)
    with pytest.raises(ValueError, match="interface"):
        stage(runtime.settings, path, digest(path), tmp_path)
    path = wheel(tmp_path)
    fake_installer(monkeypatch, runtime.settings, path)
    monkeypatch.setattr(
        "mcp_sorter.releases.subprocess.run", lambda *a, **kw: SimpleNamespace(stdout="wrong")
    )
    with pytest.raises(ValueError, match="version check"):
        stage(runtime.settings, path, digest(path), tmp_path)
    assert not (directory(runtime.settings, digest(path)) / "installed.json").exists()


def test_staging_rejects_oversized_mismatched_and_changed_wheels(runtime, tmp_path, monkeypatch):
    path = wheel(tmp_path)
    with ZipFile(path) as archive:
        payload = archive.read("mcp_sorter/release.json")
    with ZipFile(path, "w") as archive:
        archive.writestr("mcp_sorter/release.json", payload + b" " * 16_384)
    with pytest.raises(ValueError, match="Oversized"):
        stage(runtime.settings, path, digest(path), tmp_path)
    path = wheel(tmp_path)
    mismatched = path.with_name("mcp_server_sorter-9.0.0-py3-none-any.whl")
    mismatched.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="disagree"):
        stage(runtime.settings, mismatched, digest(mismatched), tmp_path)
    monkeypatch.setattr(
        "mcp_sorter.releases.shutil.copyfile",
        lambda source, destination: destination.write_bytes(b"changed"),
    )
    with pytest.raises(ValueError, match="changed while"):
        stage(runtime.settings, path, digest(path), tmp_path)


def test_first_activation_works_before_state_exists(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "new")
    path = wheel(tmp_path)
    fake_installer(monkeypatch, settings, path)
    record = stage(settings, path, digest(path), tmp_path)
    result = activate_release(settings, record.sha256)
    assert result.active == record.sha256 and result.backup is None and result.previous is None
    assert not (settings.data_dir / "state.sqlite").exists()


def test_release_selection_does_not_migrate_and_rollback_rejects_collection_history(
    runtime, tmp_path, monkeypatch
):
    settings = runtime.settings
    runtime.close()
    with closing(sqlite3.connect(settings.data_dir / "state.sqlite")) as db, db:
        db.execute("DROP TABLE collection_revisions")
        db.execute("ALTER TABLE collections DROP COLUMN current_revision")
        db.execute("ALTER TABLE collections DROP COLUMN deleted")
        db.execute("UPDATE alembic_version SET version_num='0005'")
    old = wheel(tmp_path, "0.1.1", revisions=["0005"])
    fake_installer(monkeypatch, settings, old)
    stage(settings, old, digest(old), tmp_path)
    old_pointer = activate_release(settings, digest(old))
    assert state_revision(settings.data_dir / "state.sqlite") == "0005"
    assert (
        validate_backup(settings.data_dir / "backups" / old_pointer.backup).state_revision == "0005"
    )
    candidate = wheel(tmp_path, "0.2.0", revisions=["0005", "0006"])
    fake_installer(monkeypatch, settings, candidate)
    stage(settings, candidate, digest(candidate), tmp_path)
    new_pointer = activate_release(settings, digest(candidate))
    upgraded = Runtime(settings)
    upgraded.close()
    with pytest.raises(ValueError, match="schema"):
        rollback_release(settings)
    assert pointer(settings) == new_pointer
    assert state_revision(settings.data_dir / "state.sqlite") == "0006"
