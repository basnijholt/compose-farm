"""High-level operations for compose-farm.

Contains the business logic for up, down, sync, check, and migration operations.
CLI commands are thin wrappers around these functions.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from .compose import parse_devices, parse_external_networks, parse_host_volumes
from .console import console, err_console, print_error, print_success, print_warning
from .executor import (
    CommandResult,
    check_networks_exist,
    check_paths_exist,
    check_stack_running,
    run_command,
    run_compose,
    run_compose_on_host,
)
from .state import (
    get_orphaned_stacks,
    get_stack_host,
    remove_stack,
    set_multi_host_stack,
    set_stack_host,
)

if TYPE_CHECKING:
    from .config import Config


class OperationInterruptedError(Exception):
    """Raised when a command is interrupted by Ctrl+C."""


class PreflightResult(NamedTuple):
    """Result of pre-flight checks for a stack on a host."""

    missing_paths: list[str]
    missing_networks: list[str]
    missing_devices: list[str]

    @property
    def ok(self) -> bool:
        """Return True if all checks passed."""
        return not (self.missing_paths or self.missing_networks or self.missing_devices)


async def _run_compose_step(
    cfg: Config,
    stack: str,
    command: str,
    *,
    raw: bool,
    host: str | None = None,
) -> CommandResult:
    """Run a compose command, handle raw output newline, and check for interrupts."""
    if host:
        result = await run_compose_on_host(cfg, stack, host, command, raw=raw)
    else:
        result = await run_compose(cfg, stack, command, raw=raw)
    if raw:
        print()  # Ensure newline after raw output
    if result.interrupted:
        raise OperationInterruptedError
    return result


def get_stack_paths(cfg: Config, stack: str) -> list[str]:
    """Get all required paths for a stack (compose_dir + volumes)."""
    paths = [str(cfg.compose_dir)]
    paths.extend(parse_host_volumes(cfg, stack))
    return paths


async def discover_stack_host(cfg: Config, stack: str) -> tuple[str, str | list[str] | None]:
    """Discover where a stack is running.

    For multi-host stacks, checks all assigned hosts in parallel.
    For single-host, checks assigned host first, then others.

    Returns (stack_name, host_or_hosts_or_none).
    """
    assigned_hosts = cfg.get_hosts(stack)

    if cfg.is_multi_host(stack):
        # Check all assigned hosts in parallel
        checks = await asyncio.gather(*[check_stack_running(cfg, stack, h) for h in assigned_hosts])
        running = [h for h, is_running in zip(assigned_hosts, checks, strict=True) if is_running]
        return stack, running if running else None

    # Single-host: check assigned host first, then others
    if await check_stack_running(cfg, stack, assigned_hosts[0]):
        return stack, assigned_hosts[0]
    for host in cfg.hosts:
        if host != assigned_hosts[0] and await check_stack_running(cfg, stack, host):
            return stack, host
    return stack, None


async def check_stack_requirements(
    cfg: Config,
    stack: str,
    host_name: str,
) -> PreflightResult:
    """Check if a stack can run on a specific host.

    Verifies that all required paths (volumes), networks, and devices exist.
    """
    # Check mount paths
    paths = get_stack_paths(cfg, stack)
    path_exists = await check_paths_exist(cfg, host_name, paths)
    missing_paths = [p for p, found in path_exists.items() if not found]

    # Check external networks
    networks = parse_external_networks(cfg, stack)
    missing_networks: list[str] = []
    if networks:
        net_exists = await check_networks_exist(cfg, host_name, networks)
        missing_networks = [n for n, found in net_exists.items() if not found]

    # Check devices
    devices = parse_devices(cfg, stack)
    missing_devices: list[str] = []
    if devices:
        dev_exists = await check_paths_exist(cfg, host_name, devices)
        missing_devices = [d for d, found in dev_exists.items() if not found]

    return PreflightResult(missing_paths, missing_networks, missing_devices)


async def _cleanup_and_rollback(
    cfg: Config,
    stack: str,
    target_host: str,
    current_host: str,
    prefix: str,
    *,
    was_running: bool,
    raw: bool = False,
) -> None:
    """Clean up failed start and attempt rollback to old host if it was running."""
    print_warning(f"{prefix} Cleaning up failed start on [magenta]{target_host}[/]")
    await run_compose(cfg, stack, "down", raw=raw)

    if not was_running:
        err_console.print(
            f"{prefix} [dim]Stack was not running on [magenta]{current_host}[/], skipping rollback[/]"
        )
        return

    print_warning(f"{prefix} Rolling back to [magenta]{current_host}[/]...")
    rollback_result = await run_compose_on_host(cfg, stack, current_host, "up -d", raw=raw)
    if rollback_result.success:
        print_success(f"{prefix} Rollback succeeded on [magenta]{current_host}[/]")
    else:
        print_error(f"{prefix} Rollback failed - stack is down")


def _report_preflight_failures(
    stack: str,
    target_host: str,
    preflight: PreflightResult,
) -> None:
    """Report pre-flight check failures."""
    print_error(f"[cyan]\\[{stack}][/] Cannot start on [magenta]{target_host}[/]:")
    for path in preflight.missing_paths:
        print_error(f"  missing path: {path}")
    for net in preflight.missing_networks:
        print_error(f"  missing network: {net}")
    if preflight.missing_networks:
        err_console.print(f"  [dim]Hint: cf init-network {target_host}[/]")
    for dev in preflight.missing_devices:
        print_error(f"  missing device: {dev}")


async def _up_multi_host_stack(
    cfg: Config,
    stack: str,
    prefix: str,
    *,
    raw: bool = False,
) -> list[CommandResult]:
    """Start a multi-host stack on all configured hosts."""
    host_names = cfg.get_hosts(stack)
    results: list[CommandResult] = []
    compose_path = cfg.get_compose_path(stack)
    command = f"docker compose -f {compose_path} up -d"

    # Pre-flight checks on all hosts
    for host_name in host_names:
        preflight = await check_stack_requirements(cfg, stack, host_name)
        if not preflight.ok:
            _report_preflight_failures(stack, host_name, preflight)
            results.append(CommandResult(stack=f"{stack}@{host_name}", exit_code=1, success=False))
            return results

    # Start on all hosts
    hosts_str = ", ".join(f"[magenta]{h}[/]" for h in host_names)
    console.print(f"{prefix} Starting on {hosts_str}...")

    succeeded_hosts: list[str] = []
    for host_name in host_names:
        host = cfg.hosts[host_name]
        label = f"{stack}@{host_name}"
        result = await run_command(host, command, label, stream=not raw, raw=raw)
        if raw:
            print()  # Ensure newline after raw output
        results.append(result)
        if result.success:
            succeeded_hosts.append(host_name)

    # Update state with hosts that succeeded (partial success is tracked)
    if succeeded_hosts:
        set_multi_host_stack(cfg, stack, succeeded_hosts)

    return results


async def _migrate_stack(
    cfg: Config,
    stack: str,
    current_host: str,
    target_host: str,
    prefix: str,
    *,
    raw: bool = False,
) -> CommandResult | None:
    """Migrate a stack from current_host to target_host.

    Pre-pulls/builds images on target, then stops stack on current host.
    Returns failure result if migration prep fails, None on success.
    """
    console.print(
        f"{prefix} Migrating from [magenta]{current_host}[/] → [magenta]{target_host}[/]..."
    )

    # Prepare images on target host before stopping old stack to minimize downtime.
    # Pull handles image-based compose services; build handles Dockerfile-based ones.
    # --ignore-buildable makes pull skip images that have build: defined.
    for cmd, label in [("pull --ignore-buildable", "Pull"), ("build", "Build")]:
        result = await _run_compose_step(cfg, stack, cmd, raw=raw)
        if not result.success:
            print_error(
                f"{prefix} {label} failed on [magenta]{target_host}[/], "
                "leaving stack on current host"
            )
            return result

    # Stop on current host
    down_result = await _run_compose_step(cfg, stack, "down", raw=raw, host=current_host)
    return down_result if not down_result.success else None


async def _up_single_stack(
    cfg: Config,
    stack: str,
    prefix: str,
    *,
    raw: bool,
) -> CommandResult:
    """Start a single-host stack with migration support."""
    target_host = cfg.get_hosts(stack)[0]
    current_host = get_stack_host(cfg, stack)

    # Pre-flight check: verify paths, networks, and devices exist on target
    preflight = await check_stack_requirements(cfg, stack, target_host)
    if not preflight.ok:
        _report_preflight_failures(stack, target_host, preflight)
        return CommandResult(stack=stack, exit_code=1, success=False)

    # If stack is deployed elsewhere, migrate it
    did_migration = False
    was_running = False
    if current_host and current_host != target_host:
        if current_host in cfg.hosts:
            was_running = await check_stack_running(cfg, stack, current_host)
            failure = await _migrate_stack(cfg, stack, current_host, target_host, prefix, raw=raw)
            if failure:
                return failure
            did_migration = True
        else:
            print_warning(
                f"{prefix} was on [magenta]{current_host}[/] (not in config), skipping down"
            )

    # Start on target host
    console.print(f"{prefix} Starting on [magenta]{target_host}[/]...")
    up_result = await _run_compose_step(cfg, stack, "up -d", raw=raw)

    # Update state on success, or rollback on failure
    if up_result.success:
        set_stack_host(cfg, stack, target_host)
    elif did_migration and current_host:
        await _cleanup_and_rollback(
            cfg,
            stack,
            target_host,
            current_host,
            prefix,
            was_running=was_running,
            raw=raw,
        )

    return up_result


async def up_stacks(
    cfg: Config,
    stacks: list[str],
    *,
    raw: bool = False,
) -> list[CommandResult]:
    """Start stacks with automatic migration if host changed."""
    results: list[CommandResult] = []
    total = len(stacks)

    try:
        for idx, stack in enumerate(stacks, 1):
            prefix = f"[dim][{idx}/{total}][/] [cyan]\\[{stack}][/]"

            if cfg.is_multi_host(stack):
                results.extend(await _up_multi_host_stack(cfg, stack, prefix, raw=raw))
            else:
                results.append(await _up_single_stack(cfg, stack, prefix, raw=raw))
    except OperationInterruptedError:
        raise KeyboardInterrupt from None

    return results


async def check_host_compatibility(
    cfg: Config,
    stack: str,
) -> dict[str, tuple[int, int, list[str]]]:
    """Check which hosts can run a stack based on paths, networks, and devices.

    Returns dict of host_name -> (found_count, total_count, missing_items).
    """
    # Get total requirements count
    paths = get_stack_paths(cfg, stack)
    networks = parse_external_networks(cfg, stack)
    devices = parse_devices(cfg, stack)
    total = len(paths) + len(networks) + len(devices)

    results: dict[str, tuple[int, int, list[str]]] = {}

    for host_name in cfg.hosts:
        preflight = await check_stack_requirements(cfg, stack, host_name)
        all_missing = (
            preflight.missing_paths + preflight.missing_networks + preflight.missing_devices
        )
        found = total - len(all_missing)
        results[host_name] = (found, total, all_missing)

    return results


async def stop_orphaned_stacks(cfg: Config) -> list[CommandResult]:
    """Stop orphaned stacks (in state but not in config).

    Runs docker compose down on each stack on its tracked host(s).
    Only removes from state on successful stop.

    Returns list of CommandResults for each stack@host.
    """
    orphaned = get_orphaned_stacks(cfg)
    if not orphaned:
        return []

    results: list[CommandResult] = []
    tasks: list[tuple[str, str, asyncio.Task[CommandResult]]] = []

    # Build list of (stack, host, task) for all orphaned stacks
    for stack, hosts in orphaned.items():
        host_list = hosts if isinstance(hosts, list) else [hosts]
        for host in host_list:
            # Skip hosts no longer in config
            if host not in cfg.hosts:
                print_warning(f"{stack}@{host}: host no longer in config, skipping")
                results.append(
                    CommandResult(
                        stack=f"{stack}@{host}",
                        exit_code=1,
                        success=False,
                        stderr="host no longer in config",
                    )
                )
                continue
            coro = run_compose_on_host(cfg, stack, host, "down")
            tasks.append((stack, host, asyncio.create_task(coro)))

    # Run all down commands in parallel
    if tasks:
        for stack, host, task in tasks:
            try:
                result = await task
                results.append(result)
                if result.success:
                    print_success(f"{stack}@{host}: stopped")
                else:
                    print_error(f"{stack}@{host}: {result.stderr or 'failed'}")
            except Exception as e:
                print_error(f"{stack}@{host}: {e}")
                results.append(
                    CommandResult(
                        stack=f"{stack}@{host}",
                        exit_code=1,
                        success=False,
                        stderr=str(e),
                    )
                )

    # Remove from state only for stacks where ALL hosts succeeded
    for stack, hosts in orphaned.items():
        host_list = hosts if isinstance(hosts, list) else [hosts]
        all_succeeded = all(
            r.success for r in results if r.stack.startswith(f"{stack}@") or r.stack == stack
        )
        if all_succeeded:
            remove_stack(cfg, stack)

    return results
