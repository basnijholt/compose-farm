"""Monitoring commands: logs, ps, stats."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from compose_farm.cli.app import app
from compose_farm.cli.common import (
    _STATS_PREVIEW_LIMIT,
    AllOption,
    ConfigOption,
    HostOption,
    ServiceOption,
    StacksArg,
    get_stacks,
    load_config_or_exit,
    report_results,
    run_async,
    run_parallel_with_progress,
)
from compose_farm.console import console, print_error
from compose_farm.executor import run_command, run_on_stacks
from compose_farm.state import get_stacks_needing_migration, group_stacks_by_host, load_state

if TYPE_CHECKING:
    from compose_farm.config import Config


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

    results = run_parallel_with_progress(
        "Querying hosts",
        list(cfg.hosts.keys()),
        get_count,
    )
    return dict(results)


def _build_host_table(
    cfg: Config,
    stacks_by_host: dict[str, list[str]],
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
        configured = len(stacks_by_host[host_name])
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
    table.add_row("Stacks (configured)", str(len(cfg.stacks)))
    table.add_row("Stacks (tracked)", str(len(state)))
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
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    host: HostOption = None,
    service: ServiceOption = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs")] = False,
    tail: Annotated[
        int | None,
        typer.Option("--tail", "-n", help="Number of lines (default: 20 for --all, 100 otherwise)"),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Show stack logs. With --service, shows logs for just that service."""
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config, host=host)
    if service and len(stack_list) != 1:
        print_error("--service requires exactly one stack")
        raise typer.Exit(1)

    # Default to fewer lines when showing multiple stacks
    many_stacks = all_stacks or host is not None or len(stack_list) > 1
    effective_tail = tail if tail is not None else (20 if many_stacks else 100)
    cmd = f"logs --tail {effective_tail}"
    if follow:
        cmd += " -f"
    if service:
        cmd += f" {service}"
    results = run_async(run_on_stacks(cfg, stack_list, cmd))
    report_results(results)


@app.command(rich_help_panel="Monitoring")
def ps(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    host: HostOption = None,
    service: ServiceOption = None,
    config: ConfigOption = None,
) -> None:
    """Show status of stacks.

    Without arguments: shows all stacks (same as --all).
    With stack names: shows only those stacks.
    With --host: shows stacks on that host.
    With --service: filters to a specific service within the stack.
    """
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config, host=host, default_all=True)
    if service and len(stack_list) != 1:
        print_error("--service requires exactly one stack")
        raise typer.Exit(1)
    cmd = f"ps {service}" if service else "ps"
    results = run_async(run_on_stacks(cfg, stack_list, cmd))
    report_results(results)


@app.command(rich_help_panel="Monitoring")
def stats(
    live: Annotated[
        bool,
        typer.Option("--live", "-l", help="Query Docker for live container stats"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Show overview statistics for hosts and stacks.

    Without --live: Shows config/state info (hosts, stacks, pending migrations).
    With --live: Also queries Docker on each host for container counts.
    """
    cfg = load_config_or_exit(config)
    state = load_state(cfg)
    pending = get_stacks_needing_migration(cfg)

    all_hosts = list(cfg.hosts.keys())
    stacks_by_host = group_stacks_by_host(cfg.stacks, cfg.hosts, all_hosts)
    running_by_host = group_stacks_by_host(state, cfg.hosts, all_hosts)

    container_counts: dict[str, int] = {}
    if live:
        container_counts = _get_container_counts(cfg)

    host_table = _build_host_table(
        cfg, stacks_by_host, running_by_host, container_counts, show_containers=live
    )
    console.print(host_table)

    console.print()
    console.print(_build_summary_table(cfg, state, pending))
