"""High-level operations for compose-farm.

Contains the business logic for up, down, sync, check, and migration operations.
CLI commands are thin wrappers around these functions.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .compose import parse_external_networks, parse_host_volumes
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
from .state import get_service_host, set_multi_host_service, set_service_host

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .config import Config


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
        missing_paths, missing_networks = await preflight_check(cfg, service, host_name)
        if missing_paths or missing_networks:
            report_preflight_failures(service, host_name, missing_paths, missing_networks)
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

        # Handle multi-host services separately (no migration)
        if cfg.is_multi_host(service):
            multi_results = await _up_multi_host_service(cfg, service, prefix, raw=raw)
            results.extend(multi_results)
            continue

        target_host = cfg.get_hosts(service)[0]
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


async def discover_running_services(cfg: Config) -> dict[str, str | list[str]]:
    """Discover which services are running on which hosts.

    Returns a dict mapping service names to host name(s).
    Multi-host services return a list of hosts where they're running.
    """
    discovered: dict[str, str | list[str]] = {}

    for service in cfg.services:
        assigned_hosts = cfg.get_hosts(service)

        if cfg.is_multi_host(service):
            # For multi-host services, find all hosts where it's running (check in parallel)
            checks = await asyncio.gather(
                *[check_service_running(cfg, service, h) for h in assigned_hosts]
            )
            running_hosts = [
                h for h, running in zip(assigned_hosts, checks, strict=True) if running
            ]
            if running_hosts:
                discovered[service] = running_hosts
        else:
            # Single-host service - check assigned host first
            assigned_host = assigned_hosts[0]
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


async def _check_resources(
    cfg: Config,
    services: list[str],
    get_resources: Callable[[Config, str], list[str]],
    check_exists: Callable[[Config, str, list[str]], Awaitable[dict[str, bool]]],
) -> list[tuple[str, str, str]]:
    """Generic check for resources (mounts, networks) on configured hosts."""
    missing: list[tuple[str, str, str]] = []

    for service in services:
        host_names = cfg.get_hosts(service)
        resources = get_resources(cfg, service)
        if not resources:
            continue

        for host_name in host_names:
            exists = await check_exists(cfg, host_name, resources)

            for item, found in exists.items():
                if not found:
                    missing.append((service, host_name, item))

    return missing


async def check_mounts_on_configured_hosts(
    cfg: Config,
    services: list[str],
) -> list[tuple[str, str, str]]:
    """Check mount paths exist on configured hosts.

    Returns list of (service, host, missing_path) tuples.
    """
    return await _check_resources(cfg, services, get_service_paths, check_paths_exist)


async def check_networks_on_configured_hosts(
    cfg: Config,
    services: list[str],
) -> list[tuple[str, str, str]]:
    """Check Docker networks exist on configured hosts.

    Returns list of (service, host, missing_network) tuples.
    """
    return await _check_resources(cfg, services, parse_external_networks, check_networks_exist)
