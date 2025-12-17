"""High-level operations for compose-farm.

Contains the business logic for up, down, sync, check, and migration operations.
CLI commands are thin wrappers around these functions.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from .compose import parse_devices, parse_external_networks, parse_host_volumes
from .console import console, err_console
from .executor import (
    CommandResult,
    check_networks_exist,
    check_paths_exist,
    check_service_running,
    run_command,
    run_compose,
    run_compose_on_host,
)
from .state import (
    get_orphaned_services,
    get_service_host,
    remove_service,
    set_multi_host_service,
    set_service_host,
)

if TYPE_CHECKING:
    from .config import Config


class OperationInterruptedError(Exception):
    """Raised when a command is interrupted by Ctrl+C."""


class PreflightResult(NamedTuple):
    """Result of pre-flight checks for a service on a host."""

    missing_paths: list[str]
    missing_networks: list[str]
    missing_devices: list[str]

    @property
    def ok(self) -> bool:
        """Return True if all checks passed."""
        return not (self.missing_paths or self.missing_networks or self.missing_devices)


async def _run_compose_step(
    cfg: Config,
    service: str,
    command: str,
    *,
    raw: bool,
    host: str | None = None,
) -> CommandResult:
    """Run a compose command, handle raw output newline, and check for interrupts."""
    if host:
        result = await run_compose_on_host(cfg, service, host, command, raw=raw)
    else:
        result = await run_compose(cfg, service, command, raw=raw)
    if raw:
        print()  # Ensure newline after raw output
    if result.interrupted:
        raise OperationInterruptedError
    return result


def get_service_paths(cfg: Config, service: str) -> list[str]:
    """Get all required paths for a service (compose_dir + volumes)."""
    paths = [str(cfg.compose_dir)]
    paths.extend(parse_host_volumes(cfg, service))
    return paths


async def check_service_requirements(
    cfg: Config,
    service: str,
    host_name: str,
) -> PreflightResult:
    """Check if a service can run on a specific host.

    Verifies that all required paths (volumes), networks, and devices exist.
    """
    # Check mount paths
    paths = get_service_paths(cfg, service)
    path_exists = await check_paths_exist(cfg, host_name, paths)
    missing_paths = [p for p, found in path_exists.items() if not found]

    # Check external networks
    networks = parse_external_networks(cfg, service)
    missing_networks: list[str] = []
    if networks:
        net_exists = await check_networks_exist(cfg, host_name, networks)
        missing_networks = [n for n, found in net_exists.items() if not found]

    # Check devices
    devices = parse_devices(cfg, service)
    missing_devices: list[str] = []
    if devices:
        dev_exists = await check_paths_exist(cfg, host_name, devices)
        missing_devices = [d for d, found in dev_exists.items() if not found]

    return PreflightResult(missing_paths, missing_networks, missing_devices)


async def _cleanup_and_rollback(
    cfg: Config,
    service: str,
    target_host: str,
    current_host: str,
    prefix: str,
    *,
    was_running: bool,
    raw: bool = False,
) -> None:
    """Clean up failed start and attempt rollback to old host if it was running."""
    err_console.print(
        f"{prefix} [yellow]![/] Cleaning up failed start on [magenta]{target_host}[/]"
    )
    await run_compose(cfg, service, "down", raw=raw)

    if not was_running:
        err_console.print(
            f"{prefix} [dim]Service was not running on [magenta]{current_host}[/], skipping rollback[/]"
        )
        return

    err_console.print(f"{prefix} [yellow]![/] Rolling back to [magenta]{current_host}[/]...")
    rollback_result = await run_compose_on_host(cfg, service, current_host, "up -d", raw=raw)
    if rollback_result.success:
        console.print(f"{prefix} [green]✓[/] Rollback succeeded on [magenta]{current_host}[/]")
    else:
        err_console.print(f"{prefix} [red]✗[/] Rollback failed - service is down")


def _report_preflight_failures(
    service: str,
    target_host: str,
    preflight: PreflightResult,
) -> None:
    """Report pre-flight check failures."""
    err_console.print(
        f"[cyan]\\[{service}][/] [red]✗[/] Cannot start on [magenta]{target_host}[/]:"
    )
    for path in preflight.missing_paths:
        err_console.print(f"  [red]✗[/] missing path: {path}")
    for net in preflight.missing_networks:
        err_console.print(f"  [red]✗[/] missing network: {net}")
    if preflight.missing_networks:
        err_console.print(f"  [dim]hint: cf init-network {target_host}[/]")
    for dev in preflight.missing_devices:
        err_console.print(f"  [red]✗[/] missing device: {dev}")


async def _up_multi_host_service(
    cfg: Config,
    service: str,
    prefix: str,
    *,
    raw: bool = False,
) -> list[CommandResult]:
    """Start a multi-host service on all configured hosts."""
    host_names = cfg.get_hosts(service)
    results: list[CommandResult] = []
    compose_path = cfg.get_compose_path(service)
    command = f"docker compose -f {compose_path} up -d"

    # Pre-flight checks on all hosts
    for host_name in host_names:
        preflight = await check_service_requirements(cfg, service, host_name)
        if not preflight.ok:
            _report_preflight_failures(service, host_name, preflight)
            results.append(
                CommandResult(service=f"{service}@{host_name}", exit_code=1, success=False)
            )
            return results

    # Start on all hosts
    hosts_str = ", ".join(f"[magenta]{h}[/]" for h in host_names)
    console.print(f"{prefix} Starting on {hosts_str}...")

    succeeded_hosts: list[str] = []
    for host_name in host_names:
        host = cfg.hosts[host_name]
        label = f"{service}@{host_name}"
        result = await run_command(host, command, label, stream=not raw, raw=raw)
        if raw:
            print()  # Ensure newline after raw output
        results.append(result)
        if result.success:
            succeeded_hosts.append(host_name)

    # Update state with hosts that succeeded (partial success is tracked)
    if succeeded_hosts:
        set_multi_host_service(cfg, service, succeeded_hosts)

    return results


async def _migrate_service(
    cfg: Config,
    service: str,
    current_host: str,
    target_host: str,
    prefix: str,
    *,
    raw: bool = False,
) -> CommandResult | None:
    """Migrate a service from current_host to target_host.

    Pre-pulls/builds images on target, then stops service on current host.
    Returns failure result if migration prep fails, None on success.
    """
    console.print(
        f"{prefix} Migrating from [magenta]{current_host}[/] → [magenta]{target_host}[/]..."
    )

    # Prepare images on target host before stopping old service to minimize downtime.
    # Pull handles image-based services; build handles Dockerfile-based services.
    # --ignore-buildable makes pull skip images that have build: defined.
    for cmd, label in [("pull --ignore-buildable", "Pull"), ("build", "Build")]:
        result = await _run_compose_step(cfg, service, cmd, raw=raw)
        if not result.success:
            err_console.print(
                f"{prefix} [red]✗[/] {label} failed on [magenta]{target_host}[/], "
                "leaving service on current host"
            )
            return result

    # Stop on current host
    down_result = await _run_compose_step(cfg, service, "down", raw=raw, host=current_host)
    return down_result if not down_result.success else None


async def _up_single_service(
    cfg: Config,
    service: str,
    prefix: str,
    *,
    raw: bool,
) -> CommandResult:
    """Start a single-host service with migration support."""
    target_host = cfg.get_hosts(service)[0]
    current_host = get_service_host(cfg, service)

    # Pre-flight check: verify paths, networks, and devices exist on target
    preflight = await check_service_requirements(cfg, service, target_host)
    if not preflight.ok:
        _report_preflight_failures(service, target_host, preflight)
        return CommandResult(service=service, exit_code=1, success=False)

    # If service is deployed elsewhere, migrate it
    did_migration = False
    was_running = False
    if current_host and current_host != target_host:
        if current_host in cfg.hosts:
            was_running = await check_service_running(cfg, service, current_host)
            failure = await _migrate_service(
                cfg, service, current_host, target_host, prefix, raw=raw
            )
            if failure:
                return failure
            did_migration = True
        else:
            err_console.print(
                f"{prefix} [yellow]![/] was on "
                f"[magenta]{current_host}[/] (not in config), skipping down"
            )

    # Start on target host
    console.print(f"{prefix} Starting on [magenta]{target_host}[/]...")
    up_result = await _run_compose_step(cfg, service, "up -d", raw=raw)

    # Update state on success, or rollback on failure
    if up_result.success:
        set_service_host(cfg, service, target_host)
    elif did_migration and current_host:
        await _cleanup_and_rollback(
            cfg,
            service,
            target_host,
            current_host,
            prefix,
            was_running=was_running,
            raw=raw,
        )

    return up_result


async def up_services(
    cfg: Config,
    services: list[str],
    *,
    raw: bool = False,
) -> list[CommandResult]:
    """Start services with automatic migration if host changed."""
    results: list[CommandResult] = []
    total = len(services)

    try:
        for idx, service in enumerate(services, 1):
            prefix = f"[dim][{idx}/{total}][/] [cyan]\\[{service}][/]"

            if cfg.is_multi_host(service):
                results.extend(await _up_multi_host_service(cfg, service, prefix, raw=raw))
            else:
                results.append(await _up_single_service(cfg, service, prefix, raw=raw))
    except OperationInterruptedError:
        raise KeyboardInterrupt from None

    return results


async def check_host_compatibility(
    cfg: Config,
    service: str,
) -> dict[str, tuple[int, int, list[str]]]:
    """Check which hosts can run a service based on paths, networks, and devices.

    Returns dict of host_name -> (found_count, total_count, missing_items).
    """
    # Get total requirements count
    paths = get_service_paths(cfg, service)
    networks = parse_external_networks(cfg, service)
    devices = parse_devices(cfg, service)
    total = len(paths) + len(networks) + len(devices)

    results: dict[str, tuple[int, int, list[str]]] = {}

    for host_name in cfg.hosts:
        preflight = await check_service_requirements(cfg, service, host_name)
        all_missing = (
            preflight.missing_paths + preflight.missing_networks + preflight.missing_devices
        )
        found = total - len(all_missing)
        results[host_name] = (found, total, all_missing)

    return results


async def stop_orphaned_services(cfg: Config) -> list[CommandResult]:
    """Stop orphaned services (in state but not in config).

    Runs docker compose down on each service on its tracked host(s).
    Only removes from state on successful stop.

    Returns list of CommandResults for each service@host.
    """
    orphaned = get_orphaned_services(cfg)
    if not orphaned:
        return []

    results: list[CommandResult] = []
    tasks: list[tuple[str, str, asyncio.Task[CommandResult]]] = []

    # Build list of (service, host, task) for all orphaned services
    for service, hosts in orphaned.items():
        host_list = hosts if isinstance(hosts, list) else [hosts]
        for host in host_list:
            # Skip hosts no longer in config
            if host not in cfg.hosts:
                console.print(
                    f"  [yellow]![/] {service}@{host}: host no longer in config, skipping"
                )
                results.append(
                    CommandResult(
                        service=f"{service}@{host}",
                        exit_code=1,
                        success=False,
                        stderr="host no longer in config",
                    )
                )
                continue
            coro = run_compose_on_host(cfg, service, host, "down")
            tasks.append((service, host, asyncio.create_task(coro)))

    # Run all down commands in parallel
    if tasks:
        for service, host, task in tasks:
            try:
                result = await task
                results.append(result)
                if result.success:
                    console.print(f"  [green]✓[/] {service}@{host}: stopped")
                else:
                    console.print(f"  [red]✗[/] {service}@{host}: {result.stderr or 'failed'}")
            except Exception as e:
                console.print(f"  [red]✗[/] {service}@{host}: {e}")
                results.append(
                    CommandResult(
                        service=f"{service}@{host}",
                        exit_code=1,
                        success=False,
                        stderr=str(e),
                    )
                )

    # Remove from state only for services where ALL hosts succeeded
    for service, hosts in orphaned.items():
        host_list = hosts if isinstance(hosts, list) else [hosts]
        all_succeeded = all(
            r.success
            for r in results
            if r.service.startswith(f"{service}@") or r.service == service
        )
        if all_succeeded:
            remove_service(cfg, service)

    return results
