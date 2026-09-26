import typer
import uvicorn

from mcp_sorter import __version__
from mcp_sorter.api import create_app

app = typer.Typer(no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the installed application version."""
    typer.echo(__version__)


@app.command()
def serve(port: int = 8000) -> None:
    """Serve the application on the loopback interface."""
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    app()
