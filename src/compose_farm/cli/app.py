"""Shared Typer app instance."""

from __future__ import annotations

from typing import Annotated

import typer

__all__ = ["app"]


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        # Lazy import: package version lookup is not needed while rendering `cf --help`.
        from compose_farm import __version__  # noqa: PLC0415

        typer.echo(f"compose-farm {__version__}")
        raise typer.Exit


app = typer.Typer(
    name="compose-farm",
    help="Compose Farm - run docker compose commands across multiple hosts",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    suggest_commands=False,
    rich_markup_mode="rich",
)


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
