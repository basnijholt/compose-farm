"""CLI interface using Typer."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from .config import load_config
from .ssh import (
    run_on_services,
    run_sequential_on_services,
)

app = typer.Typer(
    name="sdc",
    help="Simple Distributed Compose - run docker compose commands across hosts",
    no_args_is_help=True,
)


def _get_services(
    services: list[str],
    all_services: bool,
    config_path: Path | None,
) -> tuple[list[str], any]:
    """Resolve service list and load config."""
    config = load_config(config_path)

    if all_services:
        return list(config.services.keys()), config
    if not services:
        typer.echo("Error: Specify services or use --all", err=True)
        raise typer.Exit(1)
    return list(services), config


def _run_async(coro):
    """Run async coroutine."""
    return asyncio.run(coro)


def _report_results(results: list) -> None:
    """Report command results and exit with appropriate code."""
    failed = [r for r in results if not r.success]
    if failed:
        for r in failed:
            typer.echo(f"[{r.service}] Failed with exit code {r.exit_code}", err=True)
        raise typer.Exit(1)


ServicesArg = Annotated[
    Optional[list[str]],
    typer.Argument(help="Services to operate on"),
]
AllOption = Annotated[
    bool,
    typer.Option("--all", "-a", help="Run on all services"),
]
ConfigOption = Annotated[
    Optional[Path],
    typer.Option("--config", "-c", help="Path to config file"),
]


@app.command()
def up(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Start services (docker compose up -d)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    results = _run_async(run_on_services(cfg, svc_list, "up -d"))
    _report_results(results)


@app.command()
def down(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Stop services (docker compose down)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    results = _run_async(run_on_services(cfg, svc_list, "down"))
    _report_results(results)


@app.command()
def pull(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Pull latest images (docker compose pull)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    results = _run_async(run_on_services(cfg, svc_list, "pull"))
    _report_results(results)


@app.command()
def restart(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Restart services (down + up)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    results = _run_async(run_sequential_on_services(cfg, svc_list, ["down", "up -d"]))
    _report_results(results)


@app.command()
def update(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Update services (pull + down + up)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    results = _run_async(
        run_sequential_on_services(cfg, svc_list, ["pull", "down", "up -d"])
    )
    _report_results(results)


@app.command()
def logs(
    services: ServicesArg = None,
    all_services: AllOption = False,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs")] = False,
    tail: Annotated[int, typer.Option("--tail", "-n", help="Number of lines")] = 100,
    config: ConfigOption = None,
) -> None:
    """Show service logs."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    cmd = f"logs --tail {tail}"
    if follow:
        cmd += " -f"
    results = _run_async(run_on_services(cfg, svc_list, cmd))
    _report_results(results)


@app.command()
def ps(
    config: ConfigOption = None,
) -> None:
    """Show status of all services."""
    cfg = load_config(config)
    results = _run_async(run_on_services(cfg, list(cfg.services.keys()), "ps"))
    _report_results(results)


if __name__ == "__main__":
    app()
