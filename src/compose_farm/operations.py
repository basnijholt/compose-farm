"""High-level operations for compose-farm.

Contains the business logic for up, down, sync, check, and migration operations.
CLI commands are thin wrappers around these functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from .compose import parse_external_networks, parse_host_volumes
from .executor import (
    CommandResult,
    check_networks_exist,
    check_paths_exist,
    check_service_running,
    run_compose,
    run_compose_on_host,
)
from .state import get_service_host, set_service_host

if TYPE_CHECKING:
    from .config import Config

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


def get_service_paths(cfg: Config, service: str) -> list[str]:
    """Get all required paths for a service (compose_dir + volumes)."""
    paths = [str(cfg.compose_dir)]
    paths.extend(parse_host_volumes(cfg, service))
    return paths


async def check_mounts_for_migration(
    cfg: Config,
    service: str,
    target_host: str,
) -> list[str]:
    """Check if mount paths exist on target host. Returns list of missing paths."""
    paths = get_service_paths(cfg, service)
    exists = await check_paths_exist(cfg, target_host, paths)
    return [p for p, found in exists.items() if not found]


async def check_networks_for_migration(
    cfg: Config,
    service: str,
    target_host: str,
) -> list[str]:
    """Check if Docker networks exist on target host. Returns list of missing networks."""
    networks = parse_external_networks(cfg, service)
    if not networks:
        return []
    exists = await check_networks_exist(cfg, target_host, networks)
    return [n for n, found in exists.items() if not found]


async def preflight_check(
    cfg: Config,
    service: str,
    target_host: str,
) -> tuple[list[str], list[str]]:
    """Run pre-flight checks for a service on target host.

    Returns (missing_paths, missing_networks).
    """
    missing_paths = await check_mounts_for_migration(cfg, service, target_host)
    missing_networks = await check_networks_for_migration(cfg, service, target_host)
    return missing_paths, missing_networks


def report_preflight_failures(
    service: str,
    target_host: str,
    missing_paths: list[str],
    missing_networks: list[str],
) -> None:
    """Report pre-flight check failures."""
    err_console.print(
        f"[cyan]\\[{service}][/] [red]✗[/] Cannot start on [magenta]{target_host}[/]:"
    )
    for path in missing_paths:
        err_console.print(f"  [red]✗[/] missing path: {path}")
    for net in missing_networks:
        err_console.print(f"  [red]✗[/] missing network: {net}")


async def up_services(
    cfg: Config,
    services: list[str],
    *,
    raw: bool = False,
) -> list[CommandResult]:
    """Start services with automatic migration if host changed."""
    results: list[CommandResult] = []
    total = len(services)

    for idx, service in enumerate(services, 1):
        prefix = f"[dim][{idx}/{total}][/] [cyan]\\[{service}][/]"
        target_host = cfg.services[service]
        current_host = get_service_host(cfg, service)

        # Pre-flight check: verify paths and networks exist on target
        missing_paths, missing_networks = await preflight_check(cfg, service, target_host)
        if missing_paths or missing_networks:
            report_preflight_failures(service, target_host, missing_paths, missing_networks)
            results.append(CommandResult(service=service, exit_code=1, success=False))
            continue

        # If service is deployed elsewhere, migrate it
        if current_host and current_host != target_host:
            if current_host in cfg.hosts:
                console.print(
                    f"{prefix} Migrating from "
                    f"[magenta]{current_host}[/] → [magenta]{target_host}[/]..."
                )
                down_result = await run_compose_on_host(cfg, service, current_host, "down", raw=raw)
                if raw:
                    print()  # Ensure newline after raw output
                if not down_result.success:
                    results.append(down_result)
                    continue
            else:
                err_console.print(
                    f"{prefix} [yellow]![/] was on "
                    f"[magenta]{current_host}[/] (not in config), skipping down"
                )

        # Start on target host
        console.print(f"{prefix} Starting on [magenta]{target_host}[/]...")
        up_result = await run_compose(cfg, service, "up -d", raw=raw)
        if raw:
            print()  # Ensure newline after raw output (progress bars end with \r)
        results.append(up_result)

        # Update state on success
        if up_result.success:
            set_service_host(cfg, service, target_host)

    return results


async def discover_running_services(cfg: Config) -> dict[str, str]:
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


async def check_host_compatibility(
    cfg: Config,
    service: str,
) -> dict[str, tuple[int, int, list[str]]]:
    """Check which hosts can run a service based on mount paths.

    Returns dict of host_name -> (found_count, total_count, missing_paths).
    """
    paths = get_service_paths(cfg, service)
    results: dict[str, tuple[int, int, list[str]]] = {}

    for host_name in cfg.hosts:
        exists = await check_paths_exist(cfg, host_name, paths)
        found = sum(1 for v in exists.values() if v)
        missing = [p for p, v in exists.items() if not v]
        results[host_name] = (found, len(paths), missing)

    return results


async def check_mounts_on_configured_hosts(
    cfg: Config,
    services: list[str],
) -> list[tuple[str, str, str]]:
    """Check mount paths exist on configured hosts.

    Returns list of (service, host, missing_path) tuples.
    """
    missing: list[tuple[str, str, str]] = []

    for service in services:
        host_name = cfg.services[service]
        paths = get_service_paths(cfg, service)
        exists = await check_paths_exist(cfg, host_name, paths)

        for path, found in exists.items():
            if not found:
                missing.append((service, host_name, path))

    return missing


async def check_networks_on_configured_hosts(
    cfg: Config,
    services: list[str],
) -> list[tuple[str, str, str]]:
    """Check Docker networks exist on configured hosts.

    Returns list of (service, host, missing_network) tuples.
    """
    missing: list[tuple[str, str, str]] = []

    for service in services:
        host_name = cfg.services[service]
        networks = parse_external_networks(cfg, service)
        if not networks:
            continue
        exists = await check_networks_exist(cfg, host_name, networks)

        for net, found in exists.items():
            if not found:
                missing.append((service, host_name, net))

    return missing
