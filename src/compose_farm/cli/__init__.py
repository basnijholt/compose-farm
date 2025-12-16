"""CLI interface using Typer."""

from __future__ import annotations

from typing import Annotated

import typer

from compose_farm import __version__
from compose_farm.cli import lifecycle, management, monitoring
from compose_farm.cli.config import config_app

__all__ = ["app"]


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"compose-farm {__version__}")
        raise typer.Exit


app = typer.Typer(
    name="compose-farm",
    help="Compose Farm - run docker compose commands across multiple hosts",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

# Register command modules
lifecycle.register_commands(app)
monitoring.register_commands(app)
management.register_commands(app)

# Register config subcommand
app.add_typer(config_app, name="config", rich_help_panel="Configuration")


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-v",
            help="Show version and exit",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Compose Farm - run docker compose commands across multiple hosts."""


if __name__ == "__main__":
    app()
