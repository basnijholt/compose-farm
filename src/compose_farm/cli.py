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
    check_service_running,
    run_compose,
    run_compose_on_host,
    run_on_services,
    run_sequential_on_services,
)
from .state import get_service_host, load_state, remove_service, save_state, set_service_host
from .traefik import generate_traefik_config

if TYPE_CHECKING:
    from collections.abc import Coroutine

T = TypeVar("T")


def _maybe_regenerate_traefik(cfg: Config) -> None:
    """Regenerate traefik config if traefik_file is configured."""
    if cfg.traefik_file is None:
        return

    try:
        dynamic, warnings = generate_traefik_config(cfg, list(cfg.services.keys()))
        cfg.traefik_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.traefik_file.write_text(yaml.safe_dump(dynamic, sort_keys=False))
        typer.echo(f"Traefik config updated: {cfg.traefik_file}")
        for warning in warnings:
            typer.echo(warning, err=True)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Warning: Failed to update traefik config: {exc}", err=True)


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


async def _up_with_migration(
    cfg: Config,
    services: list[str],
) -> list[CommandResult]:
    """Start services with automatic migration if host changed."""
    results: list[CommandResult] = []

    for service in services:
        target_host = cfg.services[service]
        current_host = get_service_host(cfg, service)

        # If service is deployed elsewhere, migrate it
        if current_host and current_host != target_host:
            if current_host in cfg.hosts:
                typer.echo(f"[{service}] Migrating from {current_host} to {target_host}...")
                down_result = await run_compose_on_host(cfg, service, current_host, "down")
                if not down_result.success:
                    results.append(down_result)
                    continue
            else:
                typer.echo(
                    f"[{service}] Warning: was on {current_host} (not in config), skipping down",
                    err=True,
                )

        # Start on target host
        up_result = await run_compose(cfg, service, "up -d")
        results.append(up_result)

        # Update state on success
        if up_result.success:
            set_service_host(cfg, service, target_host)

    return results


@app.command()
def up(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Start services (docker compose up -d). Auto-migrates if host changed."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    results = _run_async(_up_with_migration(cfg, svc_list))
    _maybe_regenerate_traefik(cfg)
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

    # Remove from state on success
    for result in results:
        if result.success:
            remove_service(cfg, result.service)

    _maybe_regenerate_traefik(cfg)
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
    _maybe_regenerate_traefik(cfg)
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
    _maybe_regenerate_traefik(cfg)
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


async def _discover_running_services(cfg: Config) -> dict[str, str]:
    """Discover which services are running on which hosts.

    Returns a dict mapping service names to host names for running services.
    """
    discovered: dict[str, str] = {}

    for service, assigned_host in cfg.services.items():
        # Check assigned host first (most common case)
        if await check_service_running(cfg, service, assigned_host):
            discovered[service] = assigned_host
            continue

        # Check other hosts in case service was migrated but state is stale
        for host_name in cfg.hosts:
            if host_name == assigned_host:
                continue
            if await check_service_running(cfg, service, host_name):
                discovered[service] = host_name
                break

    return discovered


def _report_sync_changes(
    added: list[str],
    removed: list[str],
    changed: list[tuple[str, str, str]],
    discovered: dict[str, str],
    current_state: dict[str, str],
) -> None:
    """Report sync changes to the user."""
    if added:
        typer.echo(f"\nNew services found ({len(added)}):")
        for service in sorted(added):
            typer.echo(f"  + {service} on {discovered[service]}")

    if changed:
        typer.echo(f"\nServices on different hosts ({len(changed)}):")
        for service, old_host, new_host in sorted(changed):
            typer.echo(f"  ~ {service}: {old_host} -> {new_host}")

    if removed:
        typer.echo(f"\nServices no longer running ({len(removed)}):")
        for service in sorted(removed):
            typer.echo(f"  - {service} (was on {current_state[service]})")


@app.command()
def sync(
    config: ConfigOption = None,
    log_path: LogPathOption = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would be synced without writing"),
    ] = False,
) -> None:
    """Sync local state with running services.

    Discovers which services are running on which hosts, updates the state
    file, and captures image digests. Combines service discovery with
    image snapshot into a single command.
    """
    cfg = load_config(config)
    current_state = load_state(cfg)

    typer.echo("Discovering running services...")
    discovered = _run_async(_discover_running_services(cfg))

    # Calculate changes
    added = [s for s in discovered if s not in current_state]
    removed = [s for s in current_state if s not in discovered]
    changed = [
        (s, current_state[s], discovered[s])
        for s in discovered
        if s in current_state and current_state[s] != discovered[s]
    ]

    # Report state changes
    state_changed = bool(added or removed or changed)
    if state_changed:
        _report_sync_changes(added, removed, changed, discovered, current_state)
    else:
        typer.echo("State is already in sync.")

    if dry_run:
        typer.echo("\n(dry-run: no changes made)")
        return

    # Update state file
    if state_changed:
        save_state(cfg, discovered)
        typer.echo(f"\nState updated: {len(discovered)} services tracked.")

    # Capture image digests for running services
    if discovered:
        typer.echo("\nCapturing image digests...")
        try:
            path = _run_async(snapshot_services(cfg, list(discovered.keys()), log_path=log_path))
            typer.echo(f"Digests written to {path}")
        except RuntimeError as exc:
            typer.echo(f"Warning: {exc}", err=True)


if __name__ == "__main__":
    app()
