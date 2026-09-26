import json
import logging
from pathlib import Path
from typing import Literal

import typer
import uvicorn

from mcp_sorter import __version__
from mcp_sorter.api import create_app
from mcp_sorter.evaluation import (
    EvaluationReport,
    compare_reports,
    evaluate,
    report_path,
    save_report,
)
from mcp_sorter.gateways import rank_with_profile
from mcp_sorter.models import Filters
from mcp_sorter.ranking import Profile, RankRequest
from mcp_sorter.ranking import compare as compare_servers
from mcp_sorter.runtime import Runtime
from mcp_sorter.selections import catalog_diff
from mcp_sorter.settings import Settings
from mcp_sorter.sources import fetch_registry

app = typer.Typer(no_args_is_help=True)
catalog_app = typer.Typer(no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
eval_app = typer.Typer(no_args_is_help=True)
app.add_typer(eval_app, name="eval")
jobs_app = typer.Typer(no_args_is_help=True)
app.add_typer(jobs_app, name="jobs")
backup_app = typer.Typer(no_args_is_help=True)
app.add_typer(backup_app, name="backup")
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config")
release_app = typer.Typer(no_args_is_help=True)
app.add_typer(release_app, name="release")


@release_app.command("stage")
def stage_release(wheel: Path, sha256: str, wheelhouse: Path) -> None:
    """Install a pinned wheel in an isolated environment using an offline wheelhouse."""
    from mcp_sorter.releases import stage

    typer.echo(stage(Settings(), wheel, sha256, wheelhouse).model_dump_json(indent=2))


@release_app.command("activate")
def select_release(sha256: str) -> None:
    """With the service stopped, select a compatible staged wheel and retain a backup."""
    from mcp_sorter.releases import activate_release

    typer.echo(activate_release(Settings(), sha256).model_dump_json(indent=2))


@release_app.command("rollback")
def rollback_application() -> None:
    """Select the previously pinned application artifact after compatibility checks."""
    from mcp_sorter.releases import rollback_release

    typer.echo(rollback_release(Settings()).model_dump_json(indent=2))


@release_app.command("serve")
def serve_pinned_release(port: int = 8000) -> None:
    """Run the currently selected application artifact on loopback."""
    from mcp_sorter.releases import serve_release

    serve_release(Settings(), port)


@config_app.command("create")
def create_configuration(
    name: str, name_weight: float = 5, description_weight: float = 1, tags_weight: float = 2
) -> None:
    """Store an immutable ranking configuration without activating it."""
    from mcp_sorter.versioning import Configuration

    runtime = Runtime(Settings())
    try:
        typer.echo(
            runtime.configurations.publish(
                Configuration(
                    name=name,
                    name_weight=name_weight,
                    description_weight=description_weight,
                    tags_weight=tags_weight,
                )
            )
        )
    finally:
        runtime.close()


@app.command("activate")
def activate_versions(snapshot: str, configuration: str) -> None:
    """Atomically activate a compatible catalog/configuration pair, retaining collections."""
    from mcp_sorter.versioning import activate

    runtime = Runtime(Settings())
    try:
        activate(runtime.catalog, runtime.configurations, snapshot, configuration)
        typer.echo(json.dumps({"snapshot": snapshot, "configuration": configuration}))
    finally:
        runtime.close()


@backup_app.command("create")
def create_backup() -> None:
    """Capture state and immutable artifacts in a verified local backup."""
    from mcp_sorter.recovery import backup

    runtime = Runtime(Settings())
    try:
        typer.echo(str(backup(runtime)))
    finally:
        runtime.close()


@backup_app.command("verify")
def verify_backup(directory: Path) -> None:
    """Verify checksums, required artifacts, and state schema compatibility."""
    from mcp_sorter.recovery import validate_backup

    typer.echo(validate_backup(directory).model_dump_json(indent=2))


@backup_app.command("restore")
def restore_backup(directory: Path, destination: Path) -> None:
    """Restore into a new data directory with the service stopped, preserving newer collections."""
    from mcp_sorter.recovery import restore

    runtime = Runtime(Settings())
    try:
        typer.echo(json.dumps(restore(runtime, directory, destination), indent=2))
    finally:
        runtime.close()


@jobs_app.command("submit")
def submit_job(kind: str, key: str, profile: str = "baseline") -> None:
    """Queue an offline evaluation or catalog refresh with an idempotency key."""
    from mcp_sorter.jobs import JobRequest, enqueue

    runtime = Runtime(Settings())
    try:
        request = JobRequest.model_validate(
            {"kind": kind, "idempotency_key": key, "profile": profile}
        )
        typer.echo(json.dumps(enqueue(runtime, request), indent=2))
    finally:
        runtime.close()


@jobs_app.command("work")
def work_once() -> None:
    """Process one available job; expired leases are recovered before claiming work."""
    from mcp_sorter.jobs import run_once

    runtime = Runtime(Settings())
    try:
        typer.echo(json.dumps({"processed": run_once(runtime)}))
    finally:
        runtime.close()


@app.command("status")
def operations_status() -> None:
    """Inspect local jobs, fallback events and alert conditions."""
    from mcp_sorter.operations import status

    runtime = Runtime(Settings())
    try:
        typer.echo(json.dumps(status(runtime), indent=2))
    finally:
        runtime.close()


@app.command()
def version() -> None:
    """Print the installed application version."""
    typer.echo(__version__)


@app.command("mcp")
def serve_mcp() -> None:
    """Expose read-only local search and comparison tools over stdio."""
    from mcp_sorter.mcp_server import create_mcp

    runtime = Runtime(Settings())
    try:
        create_mcp(runtime).run(transport="stdio")
    finally:
        runtime.close()


@app.command("probe")
def probe_endpoint(endpoint: str) -> None:
    """Initialize and list capabilities at an explicitly approved HTTPS endpoint."""
    from mcp_sorter.probes import probe

    try:
        typer.echo(probe(Settings(), endpoint).model_dump_json(indent=2))
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def serve(port: int = 8000, host: Literal["127.0.0.1", "0.0.0.0"] = "127.0.0.1") -> None:
    """Serve the application, binding to loopback unless a host is explicitly supplied."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # RequestTelemetry emits safe request IDs/statuses; URL access logs can expose queries.
    uvicorn.run(create_app(), host=host, port=port, access_log=False)


@app.command()
def demo() -> None:
    """Install the bundled showcase catalog without making external requests."""
    runtime = Runtime(Settings(mode="demo"))
    try:
        typer.echo(json.dumps({"snapshot": runtime.catalog.active(), "mode": "demo"}))
    finally:
        runtime.close()


@app.command()
@app.command("search")
def rank(query: str, category: str | None = None, profile: Profile = "baseline") -> None:
    """Find matching servers and print evidence-linked rankings as JSON."""
    runtime = Runtime(Settings())
    try:
        result = rank_with_profile(
            runtime, RankRequest(query=query, filters=Filters(category=category), profile=profile)
        )
        typer.echo(result.model_dump_json(indent=2))
    finally:
        runtime.close()


@app.command()
def compare(ids: list[str]) -> None:
    """Compare two to four server IDs in the active catalog."""
    runtime = Runtime(Settings())
    try:
        result = compare_servers(runtime.catalog, ids)
        typer.echo(json.dumps([s.model_dump(mode="json") for s in result], indent=2))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        runtime.close()


@catalog_app.command("sync")
def sync_catalog() -> None:
    """Fetch and activate a validated catalog when live networking is enabled."""
    from datetime import UTC, datetime

    runtime = Runtime(Settings())
    try:
        records, rejected = fetch_registry(runtime.settings)
        if rejected:
            raise ValueError(f"Refresh rejected {len(rejected)} records; current catalog retained")
        snapshot = runtime.catalog.publish(records, datetime.now(UTC).isoformat())
        runtime.catalog.activate(snapshot)
        typer.echo(snapshot)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        runtime.close()


@app.command()
def versions() -> None:
    """List retained catalog versions and the active snapshot."""
    runtime = Runtime(Settings())
    try:
        typer.echo(
            json.dumps(
                {
                    "active_catalog": runtime.catalog.active(),
                    "snapshots": runtime.catalog.snapshots(),
                    "configurations": {
                        key: value.model_dump()
                        for key, value in runtime.configurations.versions().items()
                    },
                },
                indent=2,
            )
        )
    finally:
        runtime.close()


@catalog_app.command("diff")
def diff_catalogs(before: str, after: str) -> None:
    """Show added, removed, changed, and deprecated server IDs."""
    runtime = Runtime(Settings())
    try:
        typer.echo(json.dumps(catalog_diff(runtime, before, after), indent=2))
    finally:
        runtime.close()


@eval_app.command("run")
def run_evaluation(profile: Profile = "baseline") -> None:
    """Run the mock golden dataset and save immutable JSON and HTML reports."""
    runtime = Runtime(Settings())
    try:
        report = evaluate(
            runtime.catalog,
            profile=profile,
            ranker=lambda catalog, query: rank_with_profile(
                runtime, query.model_copy(update={"profile": profile})
            ),
        )
        save_report(runtime.settings.data_dir, report)
        typer.echo(report.model_dump_json(indent=2))
    finally:
        runtime.close()


@eval_app.command("compare")
def compare_evaluations(baseline: str, candidate: str) -> None:
    """Compare two previously saved evaluation IDs."""
    directory = Settings().data_dir
    reports = [
        EvaluationReport.model_validate_json(report_path(directory, identifier).read_text("utf-8"))
        for identifier in (baseline, candidate)
    ]
    result = compare_reports(*reports)
    typer.echo(json.dumps(result, indent=2))
    if not result["passes_regression_gate"]:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
