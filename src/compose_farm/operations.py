"""High-level operations for compose-farm.

Contains the business logic for up, down, sync, check, and migration operations.
CLI commands are thin wrappers around these functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compose import parse_external_networks, parse_host_volumes
from .console import console, err_console
from .executor import (
    CommandResult,
    check_networks_exist,
    check_paths_exist,
    run_command,
    run_compose,
    run_compose_on_host,
)
from .state import get_service_host, set_multi_host_service, set_service_host

if TYPE_CHECKING:
    from .config import Config


class OperationInterruptedError(Exception):
    """Raised when a command is interrupted by Ctrl+C."""


def _check_interrupted(result: CommandResult) -> None:
    """Raise if command was interrupted."""
    if result.interrupted:
        raise OperationInterruptedError


def get_service_paths(cfg: Config, service: str) -> list[str]:
    """Get all required paths for a service (compose_dir + volumes)."""
    paths = [str(cfg.compose_dir)]
    paths.extend(parse_host_volumes(cfg, service))
    return paths


async def _check_mounts_for_migration(
    cfg: Config,
    service: str,
    target_host: str,
) -> list[str]:
    """Check if mount paths exist on target host. Returns list of missing paths."""
    paths = get_service_paths(cfg, service)
    exists = await check_paths_exist(cfg, target_host, paths)
    return [p for p, found in exists.items() if not found]


async def _check_networks_for_migration(
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


async def _preflight_check(
    cfg: Config,
    service: str,
    target_host: str,
) -> tuple[list[str], list[str]]:
    """Run pre-flight checks for a service on target host.

    Returns (missing_paths, missing_networks).
    """
    missing_paths = await _check_mounts_for_migration(cfg, service, target_host)
    missing_networks = await _check_networks_for_migration(cfg, service, target_host)
    return missing_paths, missing_networks


def _report_preflight_failures(
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
        missing_paths, missing_networks = await _preflight_check(cfg, service, host_name)
        if missing_paths or missing_networks:
            _report_preflight_failures(service, host_name, missing_paths, missing_networks)
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
    # Each command is a no-op for the other type (exit 0, no work done).
    pull_result = await run_compose(cfg, service, "pull", raw=raw)
    if raw:
        print()  # Ensure newline after raw output
    _check_interrupted(pull_result)
    if not pull_result.success:
        err_console.print(
            f"{prefix} [red]✗[/] Pull failed on [magenta]{target_host}[/], "
            "leaving service on current host"
        )
        return pull_result
    build_result = await run_compose(cfg, service, "build", raw=raw)
    if raw:
        print()  # Ensure newline after raw output
    _check_interrupted(build_result)
    if not build_result.success:
        err_console.print(
            f"{prefix} [red]✗[/] Build failed on [magenta]{target_host}[/], "
            "leaving service on current host"
        )
        return build_result
    down_result = await run_compose_on_host(cfg, service, current_host, "down", raw=raw)
    if raw:
        print()  # Ensure newline after raw output
    _check_interrupted(down_result)
    if not down_result.success:
        return down_result
    return None


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

            # Handle multi-host services separately (no migration)
            if cfg.is_multi_host(service):
                multi_results = await _up_multi_host_service(cfg, service, prefix, raw=raw)
                results.extend(multi_results)
                continue

            target_host = cfg.get_hosts(service)[0]
            current_host = get_service_host(cfg, service)

            # Pre-flight check: verify paths and networks exist on target
            missing_paths, missing_networks = await _preflight_check(cfg, service, target_host)
            if missing_paths or missing_networks:
                _report_preflight_failures(service, target_host, missing_paths, missing_networks)
                results.append(CommandResult(service=service, exit_code=1, success=False))
                continue

            # If service is deployed elsewhere, migrate it
            if current_host and current_host != target_host:
                if current_host in cfg.hosts:
                    failure = await _migrate_service(
                        cfg, service, current_host, target_host, prefix, raw=raw
                    )
                    if failure:
                        results.append(failure)
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
            _check_interrupted(up_result)
            results.append(up_result)

            # Update state on success
            if up_result.success:
                set_service_host(cfg, service, target_host)
    except OperationInterruptedError:
        # Convert to KeyboardInterrupt to propagate up to CLI
        raise KeyboardInterrupt from None

    return results


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
