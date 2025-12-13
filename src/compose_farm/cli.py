"""CLI interface using Typer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypeVar

import typer
import yaml

from . import __version__
from .config import Config, load_config
from .logs import snapshot_services
from .ssh import (
    CommandResult,
    run_on_services,
    run_sequential_on_services,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

T = TypeVar("T")


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"compose-farm {__version__}")
        raise typer.Exit


app = typer.Typer(
    name="compose-farm",
    help="Compose Farm - run docker compose commands across multiple hosts",
    no_args_is_help=True,
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


def _get_services(
    services: list[str],
    all_services: bool,
    config_path: Path | None,
) -> tuple[list[str], Config]:
    """Resolve service list and load config."""
    config = load_config(config_path)

    if all_services:
        return list(config.services.keys()), config
    if not services:
        typer.echo("Error: Specify services or use --all", err=True)
        raise typer.Exit(1)
    return list(services), config


def _run_async(coro: Coroutine[None, None, T]) -> T:
    """Run async coroutine."""
    return asyncio.run(coro)


def _report_results(results: list[CommandResult]) -> None:
    """Report command results and exit with appropriate code."""
    failed = [r for r in results if not r.success]
    if failed:
        for r in failed:
            typer.echo(f"[{r.service}] Failed with exit code {r.exit_code}", err=True)
        raise typer.Exit(1)


ServicesArg = Annotated[
    list[str] | None,
    typer.Argument(help="Services to operate on"),
]
AllOption = Annotated[
    bool,
    typer.Option("--all", "-a", help="Run on all services"),
]
ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to config file"),
]
LogPathOption = Annotated[
    Path | None,
    typer.Option("--log-path", "-l", help="Path to Dockerfarm TOML log"),
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
    results = _run_async(run_sequential_on_services(cfg, svc_list, ["pull", "down", "up -d"]))
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


@app.command()
def snapshot(
    services: ServicesArg = None,
    all_services: AllOption = False,
    log_path: LogPathOption = None,
    config: ConfigOption = None,
) -> None:
    """Record current image digests into the Dockerfarm TOML log."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    try:
        path = _run_async(snapshot_services(cfg, svc_list, log_path=log_path))
    except RuntimeError as exc:  # pragma: no cover - error path
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Snapshot written to {path}")


@app.command("traefik-file")
def traefik_file(
    services: ServicesArg = None,
    all_services: AllOption = False,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write Traefik file-provider YAML to this path (stdout if omitted)",
        ),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Generate a Traefik file-provider fragment from compose Traefik labels."""
    from .traefik import generate_traefik_config

    svc_list, cfg = _get_services(services or [], all_services, config)
    try:
        dynamic, warnings = generate_traefik_config(cfg, svc_list)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    rendered = yaml.safe_dump(dynamic, sort_keys=False)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        typer.echo(f"Traefik config written to {output}")
    else:
        typer.echo(rendered)

    for warning in warnings:
        typer.echo(warning, err=True)


if __name__ == "__main__":
    app()
