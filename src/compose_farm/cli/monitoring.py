"""Monitoring commands: logs, ps, stats."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Annotated

import typer
from rich.progress import Progress, TaskID  # noqa: TC002
from rich.table import Table

from compose_farm.cli.app import app
from compose_farm.cli.common import (
    _STATS_PREVIEW_LIMIT,
    AllOption,
    ConfigOption,
    HostOption,
    ServicesArg,
    get_services,
    load_config_or_exit,
    progress_bar,
    report_results,
    run_async,
)
from compose_farm.console import console, err_console
from compose_farm.executor import run_command, run_on_services
from compose_farm.state import get_services_needing_migration, load_state

if TYPE_CHECKING:
    from collections.abc import Mapping

    from compose_farm.config import Config


def _group_services_by_host(
    services: dict[str, str | list[str]],
    hosts: Mapping[str, object],
    all_hosts: list[str] | None = None,
) -> dict[str, list[str]]:
    """Group services by their assigned host(s).

    For multi-host services (list or "all"), the service appears in multiple host lists.
    """
    by_host: dict[str, list[str]] = {h: [] for h in hosts}
    for service, host_value in services.items():
        if isinstance(host_value, list):
            # Explicit list of hosts
            for host_name in host_value:
                if host_name in by_host:
                    by_host[host_name].append(service)
        elif host_value == "all" and all_hosts:
            # "all" keyword - add to all hosts
            for host_name in all_hosts:
                if host_name in by_host:
                    by_host[host_name].append(service)
        elif host_value in by_host:
            # Single host
            by_host[host_value].append(service)
    return by_host


def _get_container_counts(cfg: Config) -> dict[str, int]:
    """Get container counts from all hosts with a progress bar."""

    async def get_count(host_name: str) -> tuple[str, int]:
        host = cfg.hosts[host_name]
        result = await run_command(host, "docker ps -q | wc -l", host_name, stream=False)
        count = 0
        if result.success:
            with contextlib.suppress(ValueError):
                count = int(result.stdout.strip())
        return host_name, count

    async def gather_with_progress(progress: Progress, task_id: TaskID) -> dict[str, int]:
        hosts = list(cfg.hosts.keys())
        tasks = [asyncio.create_task(get_count(h)) for h in hosts]
        results: dict[str, int] = {}
        for coro in asyncio.as_completed(tasks):
            host_name, count = await coro
            results[host_name] = count
            progress.update(task_id, advance=1, description=f"[cyan]{host_name}[/]")
        return results

    with progress_bar("Querying hosts", len(cfg.hosts)) as (progress, task_id):
        return asyncio.run(gather_with_progress(progress, task_id))


def _build_host_table(
    cfg: Config,
    services_by_host: dict[str, list[str]],
    running_by_host: dict[str, list[str]],
    container_counts: dict[str, int],
    *,
    show_containers: bool,
) -> Table:
    """Build the hosts table."""
    table = Table(title="Hosts", show_header=True, header_style="bold cyan")
    table.add_column("Host", style="magenta")
    table.add_column("Address")
    table.add_column("Configured", justify="right")
    table.add_column("Running", justify="right")
    if show_containers:
        table.add_column("Containers", justify="right")

    for host_name in sorted(cfg.hosts.keys()):
        host = cfg.hosts[host_name]
        configured = len(services_by_host[host_name])
        running = len(running_by_host[host_name])

        row = [
            host_name,
            host.address,
            str(configured),
            str(running) if running > 0 else "[dim]0[/]",
        ]
        if show_containers:
            count = container_counts.get(host_name, 0)
            row.append(str(count) if count > 0 else "[dim]0[/]")

        table.add_row(*row)
    return table


def _build_summary_table(
    cfg: Config, state: dict[str, str | list[str]], pending: list[str]
) -> Table:
    """Build the summary table."""
    on_disk = cfg.discover_compose_dirs()

    table = Table(title="Summary", show_header=False)
    table.add_column("Label", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Total hosts", str(len(cfg.hosts)))
    table.add_row("Services (configured)", str(len(cfg.services)))
    table.add_row("Services (tracked)", str(len(state)))
    table.add_row("Compose files on disk", str(len(on_disk)))

    if pending:
        preview = ", ".join(pending[:_STATS_PREVIEW_LIMIT])
        suffix = "..." if len(pending) > _STATS_PREVIEW_LIMIT else ""
        table.add_row("Pending migrations", f"[yellow]{len(pending)}[/] ({preview}{suffix})")
    else:
        table.add_row("Pending migrations", "[green]0[/]")

    return table


# --- Command functions ---


@app.command(rich_help_panel="Monitoring")
def logs(
    services: ServicesArg = None,
    all_services: AllOption = False,
    host: HostOption = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs")] = False,
    tail: Annotated[
        int | None,
        typer.Option("--tail", "-n", help="Number of lines (default: 20 for --all, 100 otherwise)"),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Show service logs."""
    if all_services and host is not None:
        err_console.print("[red]✗[/] Cannot use --all and --host together")
        raise typer.Exit(1)

    cfg = load_config_or_exit(config)

    # Determine service list based on options
    if host is not None:
        if host not in cfg.hosts:
            err_console.print(f"[red]✗[/] Host '{host}' not found in config")
            raise typer.Exit(1)
        # Include services where host is in the list of configured hosts
        svc_list = [s for s in cfg.services if host in cfg.get_hosts(s)]
        if not svc_list:
            err_console.print(f"[yellow]![/] No services configured for host '{host}'")
            return
    else:
        svc_list, cfg = get_services(services or [], all_services, config)

    # Default to fewer lines when showing multiple services
    many_services = all_services or host is not None or len(svc_list) > 1
    effective_tail = tail if tail is not None else (20 if many_services else 100)
    cmd = f"logs --tail {effective_tail}"
    if follow:
        cmd += " -f"
    results = run_async(run_on_services(cfg, svc_list, cmd))
    report_results(results)


@app.command(rich_help_panel="Monitoring")
def ps(
    config: ConfigOption = None,
) -> None:
    """Show status of all services."""
    cfg = load_config_or_exit(config)
    results = run_async(run_on_services(cfg, list(cfg.services.keys()), "ps"))
    report_results(results)


@app.command(rich_help_panel="Monitoring")
def stats(
    live: Annotated[
        bool,
        typer.Option("--live", "-l", help="Query Docker for live container stats"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Show overview statistics for hosts and services.

    Without --live: Shows config/state info (hosts, services, pending migrations).
    With --live: Also queries Docker on each host for container counts.
    """
    cfg = load_config_or_exit(config)
    state = load_state(cfg)
    pending = get_services_needing_migration(cfg)

    all_hosts = list(cfg.hosts.keys())
    services_by_host = _group_services_by_host(cfg.services, cfg.hosts, all_hosts)
    running_by_host = _group_services_by_host(state, cfg.hosts, all_hosts)

    container_counts: dict[str, int] = {}
    if live:
        container_counts = _get_container_counts(cfg)

    host_table = _build_host_table(
        cfg, services_by_host, running_by_host, container_counts, show_containers=live
    )
    console.print(host_table)

    console.print()
    console.print(_build_summary_table(cfg, state, pending))
