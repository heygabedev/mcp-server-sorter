import json
import logging

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
def serve(port: int = 8000) -> None:
    """Serve the application on the loopback interface."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


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
