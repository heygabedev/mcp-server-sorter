import json

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
from mcp_sorter.models import Filters
from mcp_sorter.ranking import RankRequest
from mcp_sorter.ranking import compare as compare_servers
from mcp_sorter.ranking import rank as rank_servers
from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings
from mcp_sorter.sources import fetch_registry

app = typer.Typer(no_args_is_help=True)
catalog_app = typer.Typer(no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
eval_app = typer.Typer(no_args_is_help=True)
app.add_typer(eval_app, name="eval")


@app.command()
def version() -> None:
    """Print the installed application version."""
    typer.echo(__version__)


@app.command()
def serve(port: int = 8000) -> None:
    """Serve the application on the loopback interface."""
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
def rank(query: str, category: str | None = None) -> None:
    """Find matching servers and print evidence-linked rankings as JSON."""
    runtime = Runtime(Settings())
    try:
        result = rank_servers(
            runtime.catalog, RankRequest(query=query, filters=Filters(category=category))
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


@eval_app.command("run")
def run_evaluation() -> None:
    """Run the mock golden dataset and save immutable JSON and HTML reports."""
    runtime = Runtime(Settings())
    try:
        report = evaluate(runtime.catalog)
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
