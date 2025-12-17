"""Management commands: sync, check, init-network, traefik-file."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer
from rich.progress import Progress, TaskID  # noqa: TC002

from compose_farm.cli.app import app
from compose_farm.cli.common import (
    _MISSING_PATH_PREVIEW_LIMIT,
    AllOption,
    ConfigOption,
    LogPathOption,
    ServicesArg,
    get_services,
    load_config_or_exit,
    progress_bar,
    run_async,
)

if TYPE_CHECKING:
    from compose_farm.config import Config

from compose_farm.console import console, err_console
from compose_farm.executor import (
    CommandResult,
    check_service_running,
    is_local,
    run_command,
)
from compose_farm.logs import (
    DEFAULT_LOG_PATH,
    SnapshotEntry,
    collect_service_entries,
    isoformat,
    load_existing_entries,
    merge_entries,
    write_toml,
)
from compose_farm.operations import check_host_compatibility, check_service_requirements
from compose_farm.state import get_orphaned_services, load_state, save_state
from compose_farm.traefik import generate_traefik_config, render_traefik_config

# --- Sync helpers ---


def _discover_services(cfg: Config) -> dict[str, str | list[str]]:
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

    with progress_bar("Discovering", len(cfg.services)) as (progress, task_id):
        return asyncio.run(gather_with_progress(progress, task_id))


def _snapshot_services(
    cfg: Config,
    services: list[str],
    log_path: Path | None,
) -> Path:
    """Capture image digests with a progress bar."""

    async def collect_service(service: str, now: datetime) -> list[SnapshotEntry]:
        try:
            return await collect_service_entries(cfg, service, now=now)
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
    now_iso = isoformat(now_dt)

    with progress_bar("Capturing", len(services)) as (progress, task_id):
        snapshot_entries = asyncio.run(gather_with_progress(progress, task_id, now_dt, services))

    if not snapshot_entries:
        msg = "No image digests were captured"
        raise RuntimeError(msg)

    existing_entries = load_existing_entries(effective_log_path)
    merged_entries = merge_entries(existing_entries, snapshot_entries, now_iso=now_iso)
    meta = {"generated_at": now_iso, "compose_dir": str(cfg.compose_dir)}
    write_toml(effective_log_path, meta=meta, entries=merged_entries)
    return effective_log_path


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


# --- Check helpers ---


def _check_ssh_connectivity(cfg: Config) -> list[str]:
    """Check SSH connectivity to all hosts. Returns list of unreachable hosts."""
    # Filter out local hosts - no SSH needed
    remote_hosts = [h for h in cfg.hosts if not is_local(cfg.hosts[h])]

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

    with progress_bar("Checking SSH connectivity", len(remote_hosts)) as (progress, task_id):
        return asyncio.run(gather_with_progress(progress, task_id))


def _check_service_requirements(
    cfg: Config,
    services: list[str],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Check mounts, networks, and devices for all services with a progress bar.

    Returns (mount_errors, network_errors, device_errors) where each is a list of
    (service, host, missing_item) tuples.
    """

    async def check_service(
        service: str,
    ) -> tuple[
        str,
        list[tuple[str, str, str]],
        list[tuple[str, str, str]],
        list[tuple[str, str, str]],
    ]:
        """Check requirements for a single service on all its hosts."""
        host_names = cfg.get_hosts(service)
        mount_errors: list[tuple[str, str, str]] = []
        network_errors: list[tuple[str, str, str]] = []
        device_errors: list[tuple[str, str, str]] = []

        for host_name in host_names:
            missing_paths, missing_nets, missing_devs = await check_service_requirements(
                cfg, service, host_name
            )
            mount_errors.extend((service, host_name, p) for p in missing_paths)
            network_errors.extend((service, host_name, n) for n in missing_nets)
            device_errors.extend((service, host_name, d) for d in missing_devs)

        return service, mount_errors, network_errors, device_errors

    async def gather_with_progress(
        progress: Progress, task_id: TaskID
    ) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], list[tuple[str, str, str]]]:
        tasks = [asyncio.create_task(check_service(s)) for s in services]
        all_mount_errors: list[tuple[str, str, str]] = []
        all_network_errors: list[tuple[str, str, str]] = []
        all_device_errors: list[tuple[str, str, str]] = []

        for coro in asyncio.as_completed(tasks):
            service, mount_errs, net_errs, dev_errs = await coro
            all_mount_errors.extend(mount_errs)
            all_network_errors.extend(net_errs)
            all_device_errors.extend(dev_errs)
            progress.update(task_id, advance=1, description=f"[cyan]{service}[/]")

        return all_mount_errors, all_network_errors, all_device_errors

    with progress_bar("Checking requirements", len(services)) as (progress, task_id):
        return asyncio.run(gather_with_progress(progress, task_id))


def _report_config_status(cfg: Config) -> bool:
    """Check and report config vs disk status. Returns True if errors found."""
    configured = set(cfg.services.keys())
    on_disk = cfg.discover_compose_dirs()
    unmanaged = sorted(on_disk - configured)
    missing_from_disk = sorted(configured - on_disk)

    if unmanaged:
        console.print(f"\n[yellow]Unmanaged[/] (on disk but not in config, {len(unmanaged)}):")
        for name in unmanaged:
            console.print(f"  [yellow]+[/] [cyan]{name}[/]")

    if missing_from_disk:
        console.print(f"\n[red]In config but no compose file[/] ({len(missing_from_disk)}):")
        for name in missing_from_disk:
            console.print(f"  [red]-[/] [cyan]{name}[/]")

    if not unmanaged and not missing_from_disk:
        console.print("[green]✓[/] Config matches disk")

    return bool(missing_from_disk)


def _report_orphaned_services(cfg: Config) -> bool:
    """Check for services in state but not in config. Returns True if orphans found."""
    orphaned = get_orphaned_services(cfg)

    if orphaned:
        console.print("\n[yellow]Orphaned services[/] (in state but not in config):")
        console.print(
            "[dim]Run 'cf apply' to stop them, or 'cf down --orphaned' for just orphans.[/]"
        )
        for name, hosts in sorted(orphaned.items()):
            host_str = ", ".join(hosts) if isinstance(hosts, list) else hosts
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


def _report_device_errors(device_errors: list[tuple[str, str, str]]) -> None:
    """Report device errors grouped by service."""
    by_service: dict[str, list[tuple[str, str]]] = {}
    for svc, host, dev in device_errors:
        by_service.setdefault(svc, []).append((host, dev))

    console.print(f"[red]Missing devices[/] ({len(device_errors)}):")
    for svc, items in sorted(by_service.items()):
        host = items[0][0]
        devices = [d for _, d in items]
        console.print(f"  [cyan]{svc}[/] on [magenta]{host}[/]:")
        for dev in devices:
            console.print(f"    [red]✗[/] {dev}")


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
            preview = ", ".join(missing[:_MISSING_PATH_PREVIEW_LIMIT])
            if len(missing) > _MISSING_PATH_PREVIEW_LIMIT:
                preview += f", +{len(missing) - _MISSING_PATH_PREVIEW_LIMIT} more"
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

    # Check mounts, networks, and devices
    mount_errors, network_errors, device_errors = _check_service_requirements(cfg, svc_list)

    if mount_errors:
        _report_mount_errors(mount_errors)
        has_errors = True
    if network_errors:
        _report_network_errors(network_errors)
        has_errors = True
    if device_errors:
        _report_device_errors(device_errors)
        has_errors = True
    if not mount_errors and not network_errors and not device_errors:
        console.print("[green]✓[/] All mounts, networks, and devices exist")

    if show_host_compat:
        for service in svc_list:
            console.print(f"\n[bold]Host compatibility for[/] [cyan]{service}[/]:")
            compat = run_async(check_host_compatibility(cfg, service))
            assigned_hosts = cfg.get_hosts(service)
            _report_host_compatibility(compat, assigned_hosts)

    return has_errors


# Default network settings for cross-host Docker networking
_DEFAULT_NETWORK_NAME = "mynetwork"
_DEFAULT_NETWORK_SUBNET = "172.20.0.0/16"
_DEFAULT_NETWORK_GATEWAY = "172.20.0.1"


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
    svc_list, cfg = get_services(services or [], all_services, config)
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


@app.command(rich_help_panel="Configuration")
def refresh(
    config: ConfigOption = None,
    log_path: LogPathOption = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would change without writing"),
    ] = False,
) -> None:
    """Update local state from running services.

    Discovers which services are running on which hosts, updates the state
    file, and captures image digests. This is a read operation - it updates
    your local state to match reality, not the other way around.

    Use 'cf apply' to make reality match your config (stop orphans, migrate).
    """
    cfg = load_config_or_exit(config)
    current_state = load_state(cfg)

    discovered = _discover_services(cfg)

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
            path = _snapshot_services(cfg, list(discovered.keys()), log_path)
            console.print(f"[green]✓[/] Digests written to {path}")
        except RuntimeError as exc:
            err_console.print(f"[yellow]![/] {exc}")


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
    cfg = load_config_or_exit(config)

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


@app.command("init-network", rich_help_panel="Configuration")
def init_network(
    hosts: Annotated[
        list[str] | None,
        typer.Argument(help="Hosts to create network on (default: all)"),
    ] = None,
    network: Annotated[
        str,
        typer.Option("--network", "-n", help="Network name"),
    ] = _DEFAULT_NETWORK_NAME,
    subnet: Annotated[
        str,
        typer.Option("--subnet", "-s", help="Network subnet"),
    ] = _DEFAULT_NETWORK_SUBNET,
    gateway: Annotated[
        str,
        typer.Option("--gateway", "-g", help="Network gateway"),
    ] = _DEFAULT_NETWORK_GATEWAY,
    config: ConfigOption = None,
) -> None:
    """Create Docker network on hosts with consistent settings.

    Creates an external Docker network that services can use for cross-host
    communication. Uses the same subnet/gateway on all hosts to ensure
    consistent networking.
    """
    cfg = load_config_or_exit(config)

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

    results = run_async(run_all())
    failed = [r for r in results if not r.success]
    if failed:
        raise typer.Exit(1)
