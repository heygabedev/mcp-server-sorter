import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from mcp_sorter.cli import app
from mcp_sorter.recovery import digest
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
                    "readable_state_revisions": revisions or ["0005"],
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
