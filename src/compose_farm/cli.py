"""CLI interface using Typer."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypeVar

import typer
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import __version__
from .compose import parse_external_networks
from .config import Config, load_config
from .console import console, err_console
from .executor import (
    CommandResult,
    _is_local,
    check_networks_exist,
    check_paths_exist,
    check_service_running,
    run_command,
    run_compose_on_host,
    run_on_services,
    run_sequential_on_services,
)
from .logs import (
    DEFAULT_LOG_PATH,
    SnapshotEntry,
    _collect_service_entries,
    _isoformat,
    _load_existing_entries,
    _merge_entries,
    _write_toml,
)
from .operations import (
    check_host_compatibility,
    get_service_paths,
    up_services,
)
from .state import (
    add_service_to_host,
    get_services_needing_migration,
    load_state,
    remove_service,
    remove_service_from_host,
    save_state,
)
from .traefik import generate_traefik_config, render_traefik_config

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator, Mapping

T = TypeVar("T")


@contextlib.contextmanager
def _progress_bar(label: str, total: int) -> Generator[tuple[Progress, TaskID], None, None]:
    """Create a standardized progress bar with consistent styling.

    Yields (progress, task_id). Use progress.update(task_id, advance=1, description=...)
    to advance.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn(f"[bold blue]{label}[/]"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("", total=total)
        yield progress, task_id


def _load_config_or_exit(config_path: Path | None) -> Config:
    """Load config or exit with a friendly error message."""
    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        err_console.print(f"[red]✗[/] {e}")
        raise typer.Exit(1) from e


def _maybe_regenerate_traefik(cfg: Config) -> None:
    """Regenerate traefik config if traefik_file is configured."""
    if cfg.traefik_file is None:
        return

    try:
        dynamic, warnings = generate_traefik_config(cfg, list(cfg.services.keys()))
        new_content = render_traefik_config(dynamic)

        # Check if content changed
        old_content = ""
        if cfg.traefik_file.exists():
            old_content = cfg.traefik_file.read_text()

        if new_content != old_content:
            cfg.traefik_file.parent.mkdir(parents=True, exist_ok=True)
            cfg.traefik_file.write_text(new_content)
            console.print()  # Ensure we're on a new line after streaming output
            console.print(f"[green]✓[/] Traefik config updated: {cfg.traefik_file}")

        for warning in warnings:
            err_console.print(f"[yellow]![/] {warning}")
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"[yellow]![/] Failed to update traefik config: {exc}")


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
    config = _load_config_or_exit(config_path)

    if all_services:
        return list(config.services.keys()), config
    if not services:
        err_console.print("[red]✗[/] Specify services or use --all")
        raise typer.Exit(1)
    return list(services), config


def _run_async(coro: Coroutine[None, None, T]) -> T:
    """Run async coroutine."""
    return asyncio.run(coro)


def _report_results(results: list[CommandResult]) -> None:
    """Report command results and exit with appropriate code."""
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    # Always print summary when there are multiple results
    if len(results) > 1:
        console.print()  # Blank line before summary
        if failed:
            for r in failed:
                err_console.print(
                    f"[red]✗[/] [cyan]{r.service}[/] failed with exit code {r.exit_code}"
                )
            console.print()
            console.print(
                f"[green]✓[/] {len(succeeded)}/{len(results)} services succeeded, "
                f"[red]✗[/] {len(failed)} failed"
            )
        else:
            console.print(f"[green]✓[/] All {len(results)} services succeeded")

    elif failed:
        # Single service failed
        r = failed[0]
        err_console.print(f"[red]✗[/] [cyan]{r.service}[/] failed with exit code {r.exit_code}")

    if failed:
        raise typer.Exit(1)


def _run_host_operation(
    cfg: Config,
    svc_list: list[str],
    host: str,
    command: str,
    action_verb: str,
    state_callback: Callable[[Config, str, str], None],
) -> None:
    """Run an operation on a specific host for multiple services."""
    results: list[CommandResult] = []
    for service in svc_list:
        _validate_host_for_service(cfg, service, host)
        console.print(f"[cyan]\\[{service}][/] {action_verb} on [magenta]{host}[/]...")
        result = _run_async(run_compose_on_host(cfg, service, host, command, raw=True))
        print()  # Newline after raw output
        results.append(result)
        if result.success:
            state_callback(cfg, service, host)
    _maybe_regenerate_traefik(cfg)
    _report_results(results)


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
HostOption = Annotated[
    str | None,
    typer.Option("--host", "-H", help="Filter to services on this host"),
]

MISSING_PATH_PREVIEW_LIMIT = 2


def _validate_host_for_service(cfg: Config, service: str, host: str) -> None:
    """Validate that a host is valid for a service."""
    if host not in cfg.hosts:
        err_console.print(f"[red]✗[/] Host '{host}' not found in config")
        raise typer.Exit(1)
    allowed_hosts = cfg.get_hosts(service)
    if host not in allowed_hosts:
        err_console.print(
            f"[red]✗[/] Service '{service}' is not configured for host '{host}' "
            f"(configured: {', '.join(allowed_hosts)})"
        )
        raise typer.Exit(1)


@app.command(rich_help_panel="Lifecycle")
def up(
    services: ServicesArg = None,
    all_services: AllOption = False,
    migrate: Annotated[
        bool, typer.Option("--migrate", "-m", help="Only services needing migration")
    ] = False,
    host: HostOption = None,
    config: ConfigOption = None,
) -> None:
    """Start services (docker compose up -d). Auto-migrates if host changed."""
    if migrate and host:
        err_console.print("[red]✗[/] Cannot use --migrate and --host together")
        raise typer.Exit(1)

    if migrate:
        cfg = _load_config_or_exit(config)
        svc_list = get_services_needing_migration(cfg)
        if not svc_list:
            console.print("[green]✓[/] No services need migration")
            return
        console.print(f"[cyan]Migrating {len(svc_list)} service(s):[/] {', '.join(svc_list)}")
    else:
        svc_list, cfg = _get_services(services or [], all_services, config)

    # Per-host operation: run on specific host only
    if host:
        _run_host_operation(cfg, svc_list, host, "up -d", "Starting", add_service_to_host)
        return

    # Normal operation: use up_services with migration logic
    results = _run_async(up_services(cfg, svc_list, raw=True))
    _maybe_regenerate_traefik(cfg)
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def down(
    services: ServicesArg = None,
    all_services: AllOption = False,
    host: HostOption = None,
    config: ConfigOption = None,
) -> None:
    """Stop services (docker compose down)."""
    svc_list, cfg = _get_services(services or [], all_services, config)

    # Per-host operation: run on specific host only
    if host:
        _run_host_operation(cfg, svc_list, host, "down", "Stopping", remove_service_from_host)
        return

    # Normal operation
    raw = len(svc_list) == 1
    results = _run_async(run_on_services(cfg, svc_list, "down", raw=raw))

    # Remove from state on success
    # For multi-host services, result.service is "svc@host", extract base name
    removed_services: set[str] = set()
    for result in results:
        if result.success:
            base_service = result.service.split("@")[0]
            if base_service not in removed_services:
                remove_service(cfg, base_service)
                removed_services.add(base_service)

    _maybe_regenerate_traefik(cfg)
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def pull(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Pull latest images (docker compose pull)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = _run_async(run_on_services(cfg, svc_list, "pull", raw=raw))
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def restart(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Restart services (down + up)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = _run_async(run_sequential_on_services(cfg, svc_list, ["down", "up -d"], raw=raw))
    _maybe_regenerate_traefik(cfg)
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def update(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Update services (pull + down + up)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = _run_async(
        run_sequential_on_services(cfg, svc_list, ["pull", "down", "up -d"], raw=raw)
    )
    _maybe_regenerate_traefik(cfg)
    _report_results(results)


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

    cfg = _load_config_or_exit(config)

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
        svc_list, cfg = _get_services(services or [], all_services, config)

    # Default to fewer lines when showing multiple services
    many_services = all_services or host is not None or len(svc_list) > 1
    effective_tail = tail if tail is not None else (20 if many_services else 100)
    cmd = f"logs --tail {effective_tail}"
    if follow:
        cmd += " -f"
    results = _run_async(run_on_services(cfg, svc_list, cmd))
    _report_results(results)


@app.command(rich_help_panel="Monitoring")
def ps(
    config: ConfigOption = None,
) -> None:
    """Show status of all services."""
    cfg = _load_config_or_exit(config)
    results = _run_async(run_on_services(cfg, list(cfg.services.keys()), "ps"))
    _report_results(results)


_STATS_PREVIEW_LIMIT = 3  # Max number of pending migrations to show by name


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


def _get_container_counts_with_progress(cfg: Config) -> dict[str, int]:
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

    with _progress_bar("Querying hosts", len(cfg.hosts)) as (progress, task_id):
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
    cfg = _load_config_or_exit(config)
    state = load_state(cfg)
    pending = get_services_needing_migration(cfg)

    all_hosts = list(cfg.hosts.keys())
    services_by_host = _group_services_by_host(cfg.services, cfg.hosts, all_hosts)
    running_by_host = _group_services_by_host(state, cfg.hosts, all_hosts)

    container_counts: dict[str, int] = {}
    if live:
        container_counts = _get_container_counts_with_progress(cfg)

    host_table = _build_host_table(
        cfg, services_by_host, running_by_host, container_counts, show_containers=live
    )
    console.print(host_table)

    console.print()
    console.print(_build_summary_table(cfg, state, pending))


@app.command("traefik-file", rich_help_panel="Configuration")
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
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from exc

    rendered = render_traefik_config(dynamic)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        console.print(f"[green]✓[/] Traefik config written to {output}")
    else:
        console.print(rendered)

    for warning in warnings:
        err_console.print(f"[yellow]![/] {warning}")


def _discover_services_with_progress(cfg: Config) -> dict[str, str | list[str]]:
    """Discover running services with a progress bar."""

    async def check_service(service: str) -> tuple[str, str | list[str] | None]:
        """Check where a service is running.

        For multi-host services, returns list of hosts where running.
        For single-host, returns single host name or None.
        """
        assigned_hosts = cfg.get_hosts(service)

        if cfg.is_multi_host(service):
            # Multi-host: find all hosts where running (check in parallel)
            checks = await asyncio.gather(
                *[check_service_running(cfg, service, h) for h in assigned_hosts]
            )
            running_hosts = [
                h for h, running in zip(assigned_hosts, checks, strict=True) if running
            ]
            return service, running_hosts if running_hosts else None

        # Single-host: check assigned host first
        assigned_host = assigned_hosts[0]
        if await check_service_running(cfg, service, assigned_host):
            return service, assigned_host
        # Check other hosts
        for host_name in cfg.hosts:
            if host_name == assigned_host:
                continue
            if await check_service_running(cfg, service, host_name):
                return service, host_name
        return service, None

    async def gather_with_progress(
        progress: Progress, task_id: TaskID
    ) -> dict[str, str | list[str]]:
        services = list(cfg.services.keys())
        tasks = [asyncio.create_task(check_service(s)) for s in services]
        discovered: dict[str, str | list[str]] = {}
        for coro in asyncio.as_completed(tasks):
            service, host = await coro
            if host is not None:
                discovered[service] = host
            progress.update(task_id, advance=1, description=f"[cyan]{service}[/]")
        return discovered

    with _progress_bar("Discovering", len(cfg.services)) as (progress, task_id):
        return asyncio.run(gather_with_progress(progress, task_id))


def _snapshot_services_with_progress(
    cfg: Config,
    services: list[str],
    log_path: Path | None,
) -> Path:
    """Capture image digests with a progress bar."""

    async def collect_service(service: str, now: datetime) -> list[SnapshotEntry]:
        try:
            return await _collect_service_entries(cfg, service, now=now)
        except RuntimeError:
            return []

    async def gather_with_progress(
        progress: Progress, task_id: TaskID, now: datetime, svc_list: list[str]
    ) -> list[SnapshotEntry]:
        # Map tasks to service names so we can update description
        task_to_service = {asyncio.create_task(collect_service(s, now)): s for s in svc_list}
        all_entries: list[SnapshotEntry] = []
        for coro in asyncio.as_completed(list(task_to_service.keys())):
            entries = await coro
            all_entries.extend(entries)
            # Find which service just completed (by checking done tasks)
            for t, svc in task_to_service.items():
                if t.done() and not hasattr(t, "_reported"):
                    t._reported = True  # type: ignore[attr-defined]
                    progress.update(task_id, advance=1, description=f"[cyan]{svc}[/]")
                    break
        return all_entries

    effective_log_path = log_path or DEFAULT_LOG_PATH
    now_dt = datetime.now(UTC)
    now_iso = _isoformat(now_dt)

    with _progress_bar("Capturing", len(services)) as (progress, task_id):
        snapshot_entries = asyncio.run(gather_with_progress(progress, task_id, now_dt, services))

    if not snapshot_entries:
        msg = "No image digests were captured"
        raise RuntimeError(msg)

    existing_entries = _load_existing_entries(effective_log_path)
    merged_entries = _merge_entries(existing_entries, snapshot_entries, now_iso=now_iso)
    meta = {"generated_at": now_iso, "compose_dir": str(cfg.compose_dir)}
    _write_toml(effective_log_path, meta=meta, entries=merged_entries)
    return effective_log_path


def _check_ssh_connectivity(cfg: Config) -> list[str]:
    """Check SSH connectivity to all hosts. Returns list of unreachable hosts."""
    # Filter out local hosts - no SSH needed
    remote_hosts = [h for h in cfg.hosts if not _is_local(cfg.hosts[h])]

    if not remote_hosts:
        return []

    console.print()  # Spacing before progress bar

    async def check_host(host_name: str) -> tuple[str, bool]:
        host = cfg.hosts[host_name]
        result = await run_command(host, "echo ok", host_name, stream=False)
        return host_name, result.success

    async def gather_with_progress(progress: Progress, task_id: TaskID) -> list[str]:
        tasks = [asyncio.create_task(check_host(h)) for h in remote_hosts]
        unreachable: list[str] = []
        for coro in asyncio.as_completed(tasks):
            host_name, success = await coro
            if not success:
                unreachable.append(host_name)
            progress.update(task_id, advance=1, description=f"[cyan]{host_name}[/]")
        return unreachable

    with _progress_bar("Checking SSH connectivity", len(remote_hosts)) as (progress, task_id):
        return asyncio.run(gather_with_progress(progress, task_id))


def _check_mounts_and_networks_with_progress(
    cfg: Config,
    services: list[str],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Check mounts and networks for all services with a progress bar.

    Returns (mount_errors, network_errors) where each is a list of
    (service, host, missing_item) tuples.
    """

    async def check_service(
        service: str,
    ) -> tuple[str, list[tuple[str, str, str]], list[tuple[str, str, str]]]:
        """Check mounts and networks for a single service."""
        host_names = cfg.get_hosts(service)
        mount_errors: list[tuple[str, str, str]] = []
        network_errors: list[tuple[str, str, str]] = []

        # Check mounts on all hosts
        paths = get_service_paths(cfg, service)
        for host_name in host_names:
            path_exists = await check_paths_exist(cfg, host_name, paths)
            for path, found in path_exists.items():
                if not found:
                    mount_errors.append((service, host_name, path))

        # Check networks on all hosts
        networks = parse_external_networks(cfg, service)
        if networks:
            for host_name in host_names:
                net_exists = await check_networks_exist(cfg, host_name, networks)
                for net, found in net_exists.items():
                    if not found:
                        network_errors.append((service, host_name, net))

        return service, mount_errors, network_errors

    async def gather_with_progress(
        progress: Progress, task_id: TaskID
    ) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
        tasks = [asyncio.create_task(check_service(s)) for s in services]
        all_mount_errors: list[tuple[str, str, str]] = []
        all_network_errors: list[tuple[str, str, str]] = []

        for coro in asyncio.as_completed(tasks):
            service, mount_errs, net_errs = await coro
            all_mount_errors.extend(mount_errs)
            all_network_errors.extend(net_errs)
            progress.update(task_id, advance=1, description=f"[cyan]{service}[/]")

        return all_mount_errors, all_network_errors

    with _progress_bar("Checking mounts/networks", len(services)) as (progress, task_id):
        return asyncio.run(gather_with_progress(progress, task_id))


def _format_host(host: str | list[str]) -> str:
    """Format a host value for display."""
    if isinstance(host, list):
        return ", ".join(host)
    return host


def _report_sync_changes(
    added: list[str],
    removed: list[str],
    changed: list[tuple[str, str | list[str], str | list[str]]],
    discovered: dict[str, str | list[str]],
    current_state: dict[str, str | list[str]],
) -> None:
    """Report sync changes to the user."""
    if added:
        console.print(f"\nNew services found ({len(added)}):")
        for service in sorted(added):
            host_str = _format_host(discovered[service])
            console.print(f"  [green]+[/] [cyan]{service}[/] on [magenta]{host_str}[/]")

    if changed:
        console.print(f"\nServices on different hosts ({len(changed)}):")
        for service, old_host, new_host in sorted(changed):
            old_str = _format_host(old_host)
            new_str = _format_host(new_host)
            console.print(
                f"  [yellow]~[/] [cyan]{service}[/]: [magenta]{old_str}[/] → [magenta]{new_str}[/]"
            )

    if removed:
        console.print(f"\nServices no longer running ({len(removed)}):")
        for service in sorted(removed):
            host_str = _format_host(current_state[service])
            console.print(f"  [red]-[/] [cyan]{service}[/] (was on [magenta]{host_str}[/])")


@app.command(rich_help_panel="Configuration")
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
    cfg = _load_config_or_exit(config)
    current_state = load_state(cfg)

    discovered = _discover_services_with_progress(cfg)

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
        console.print("[green]✓[/] State is already in sync.")

    if dry_run:
        console.print("\n[dim](dry-run: no changes made)[/]")
        return

    # Update state file
    if state_changed:
        save_state(cfg, discovered)
        console.print(f"\n[green]✓[/] State updated: {len(discovered)} services tracked.")

    # Capture image digests for running services
    if discovered:
        try:
            path = _snapshot_services_with_progress(cfg, list(discovered.keys()), log_path)
            console.print(f"[green]✓[/] Digests written to {path}")
        except RuntimeError as exc:
            err_console.print(f"[yellow]![/] {exc}")


def _report_config_status(cfg: Config) -> bool:
    """Check and report config vs disk status. Returns True if errors found."""
    configured = set(cfg.services.keys())
    on_disk = cfg.discover_compose_dirs()
    missing_from_config = sorted(on_disk - configured)
    missing_from_disk = sorted(configured - on_disk)

    if missing_from_config:
        console.print(f"\n[yellow]On disk but not in config[/] ({len(missing_from_config)}):")
        for name in missing_from_config:
            console.print(f"  [yellow]+[/] [cyan]{name}[/]")

    if missing_from_disk:
        console.print(f"\n[red]In config but no compose file[/] ({len(missing_from_disk)}):")
        for name in missing_from_disk:
            console.print(f"  [red]-[/] [cyan]{name}[/]")

    if not missing_from_config and not missing_from_disk:
        console.print("[green]✓[/] Config matches disk")

    return bool(missing_from_disk)


def _report_orphaned_services(cfg: Config) -> bool:
    """Check for services in state but not in config. Returns True if orphans found."""
    state = load_state(cfg)
    configured = set(cfg.services.keys())
    tracked = set(state.keys())
    orphaned = sorted(tracked - configured)

    if orphaned:
        console.print("\n[yellow]Orphaned services[/] (in state but not in config):")
        console.print("[dim]These may still be running. Use 'docker compose down' to stop them.[/]")
        for name in orphaned:
            host = state[name]
            host_str = ", ".join(host) if isinstance(host, list) else host
            console.print(f"  [yellow]![/] [cyan]{name}[/] on [magenta]{host_str}[/]")
        return True

    return False


def _report_traefik_status(cfg: Config, services: list[str]) -> None:
    """Check and report traefik label status."""
    try:
        _, warnings = generate_traefik_config(cfg, services, check_all=True)
    except (FileNotFoundError, ValueError):
        return

    if warnings:
        console.print(f"\n[yellow]Traefik issues[/] ({len(warnings)}):")
        for warning in warnings:
            console.print(f"  [yellow]![/] {warning}")
    else:
        console.print("[green]✓[/] Traefik labels valid")


def _report_mount_errors(mount_errors: list[tuple[str, str, str]]) -> None:
    """Report mount errors grouped by service."""
    by_service: dict[str, list[tuple[str, str]]] = {}
    for svc, host, path in mount_errors:
        by_service.setdefault(svc, []).append((host, path))

    console.print(f"[red]Missing mounts[/] ({len(mount_errors)}):")
    for svc, items in sorted(by_service.items()):
        host = items[0][0]
        paths = [p for _, p in items]
        console.print(f"  [cyan]{svc}[/] on [magenta]{host}[/]:")
        for path in paths:
            console.print(f"    [red]✗[/] {path}")


def _report_network_errors(network_errors: list[tuple[str, str, str]]) -> None:
    """Report network errors grouped by service."""
    by_service: dict[str, list[tuple[str, str]]] = {}
    for svc, host, net in network_errors:
        by_service.setdefault(svc, []).append((host, net))

    console.print(f"[red]Missing networks[/] ({len(network_errors)}):")
    for svc, items in sorted(by_service.items()):
        host = items[0][0]
        networks = [n for _, n in items]
        console.print(f"  [cyan]{svc}[/] on [magenta]{host}[/]:")
        for net in networks:
            console.print(f"    [red]✗[/] {net}")


def _report_ssh_status(unreachable_hosts: list[str]) -> bool:
    """Report SSH connectivity status. Returns True if there are errors."""
    if unreachable_hosts:
        console.print(f"[red]Unreachable hosts[/] ({len(unreachable_hosts)}):")
        for host in sorted(unreachable_hosts):
            console.print(f"  [red]✗[/] [magenta]{host}[/]")
        return True
    console.print("[green]✓[/] All hosts reachable")
    return False


def _report_host_compatibility(
    compat: dict[str, tuple[int, int, list[str]]],
    assigned_hosts: list[str],
) -> None:
    """Report host compatibility for a service."""
    for host_name, (found, total, missing) in sorted(compat.items()):
        is_assigned = host_name in assigned_hosts
        marker = " [dim](assigned)[/]" if is_assigned else ""

        if found == total:
            console.print(f"  [green]✓[/] [magenta]{host_name}[/] {found}/{total}{marker}")
        else:
            preview = ", ".join(missing[:MISSING_PATH_PREVIEW_LIMIT])
            if len(missing) > MISSING_PATH_PREVIEW_LIMIT:
                preview += f", +{len(missing) - MISSING_PATH_PREVIEW_LIMIT} more"
            console.print(
                f"  [red]✗[/] [magenta]{host_name}[/] {found}/{total} "
                f"[dim](missing: {preview})[/]{marker}"
            )


def _run_remote_checks(cfg: Config, svc_list: list[str], *, show_host_compat: bool) -> bool:
    """Run SSH-based checks for mounts, networks, and host compatibility.

    Returns True if any errors were found.
    """
    has_errors = False

    # Check SSH connectivity first
    if _report_ssh_status(_check_ssh_connectivity(cfg)):
        has_errors = True

    console.print()  # Spacing before mounts/networks check

    # Check mounts and networks
    mount_errors, network_errors = _check_mounts_and_networks_with_progress(cfg, svc_list)

    if mount_errors:
        _report_mount_errors(mount_errors)
        has_errors = True
    if network_errors:
        _report_network_errors(network_errors)
        has_errors = True
    if not mount_errors and not network_errors:
        console.print("[green]✓[/] All mounts and networks exist")

    if show_host_compat:
        for service in svc_list:
            console.print(f"\n[bold]Host compatibility for[/] [cyan]{service}[/]:")
            compat = _run_async(check_host_compatibility(cfg, service))
            assigned_hosts = cfg.get_hosts(service)
            _report_host_compatibility(compat, assigned_hosts)

    return has_errors


@app.command(rich_help_panel="Configuration")
def check(
    services: ServicesArg = None,
    local: Annotated[
        bool,
        typer.Option("--local", help="Skip SSH-based checks (faster)"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Validate configuration, traefik labels, mounts, and networks.

    Without arguments: validates all services against configured hosts.
    With service arguments: validates specific services and shows host compatibility.

    Use --local to skip SSH-based checks for faster validation.
    """
    cfg = _load_config_or_exit(config)

    # Determine which services to check and whether to show host compatibility
    if services:
        svc_list = list(services)
        invalid = [s for s in svc_list if s not in cfg.services]
        if invalid:
            for svc in invalid:
                err_console.print(f"[red]✗[/] Service '{svc}' not found in config")
            raise typer.Exit(1)
        show_host_compat = True
    else:
        svc_list = list(cfg.services.keys())
        show_host_compat = False

    # Run checks
    has_errors = _report_config_status(cfg)
    _report_traefik_status(cfg, svc_list)

    if not local and _run_remote_checks(cfg, svc_list, show_host_compat=show_host_compat):
        has_errors = True

    # Check for orphaned services (in state but removed from config)
    if _report_orphaned_services(cfg):
        has_errors = True

    if has_errors:
        raise typer.Exit(1)


# Default network settings for cross-host Docker networking
DEFAULT_NETWORK_NAME = "mynetwork"
DEFAULT_NETWORK_SUBNET = "172.20.0.0/16"
DEFAULT_NETWORK_GATEWAY = "172.20.0.1"


@app.command("init-network", rich_help_panel="Configuration")
def init_network(
    hosts: Annotated[
        list[str] | None,
        typer.Argument(help="Hosts to create network on (default: all)"),
    ] = None,
    network: Annotated[
        str,
        typer.Option("--network", "-n", help="Network name"),
    ] = DEFAULT_NETWORK_NAME,
    subnet: Annotated[
        str,
        typer.Option("--subnet", "-s", help="Network subnet"),
    ] = DEFAULT_NETWORK_SUBNET,
    gateway: Annotated[
        str,
        typer.Option("--gateway", "-g", help="Network gateway"),
    ] = DEFAULT_NETWORK_GATEWAY,
    config: ConfigOption = None,
) -> None:
    """Create Docker network on hosts with consistent settings.

    Creates an external Docker network that services can use for cross-host
    communication. Uses the same subnet/gateway on all hosts to ensure
    consistent networking.
    """
    cfg = _load_config_or_exit(config)

    target_hosts = list(hosts) if hosts else list(cfg.hosts.keys())
    invalid = [h for h in target_hosts if h not in cfg.hosts]
    if invalid:
        for h in invalid:
            err_console.print(f"[red]✗[/] Host '{h}' not found in config")
        raise typer.Exit(1)

    async def create_network_on_host(host_name: str) -> CommandResult:
        host = cfg.hosts[host_name]
        # Check if network already exists
        check_cmd = f"docker network inspect '{network}' >/dev/null 2>&1"
        check_result = await run_command(host, check_cmd, host_name, stream=False)

        if check_result.success:
            console.print(f"[cyan]\\[{host_name}][/] Network '{network}' already exists")
            return CommandResult(service=host_name, exit_code=0, success=True)

        # Create the network
        create_cmd = (
            f"docker network create "
            f"--driver bridge "
            f"--subnet '{subnet}' "
            f"--gateway '{gateway}' "
            f"'{network}'"
        )
        result = await run_command(host, create_cmd, host_name, stream=False)

        if result.success:
            console.print(f"[cyan]\\[{host_name}][/] [green]✓[/] Created network '{network}'")
        else:
            err_console.print(
                f"[cyan]\\[{host_name}][/] [red]✗[/] Failed to create network: "
                f"{result.stderr.strip()}"
            )

        return result

    async def run_all() -> list[CommandResult]:
        return await asyncio.gather(*[create_network_on_host(h) for h in target_hosts])

    results = _run_async(run_all())
    failed = [r for r in results if not r.success]
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
