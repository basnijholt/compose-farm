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


class StackDiscoveryResult(NamedTuple):
    """Result of discovering where a stack is running across all hosts."""

    stack: str
    configured_hosts: list[str]  # From config (where it SHOULD run)
    running_hosts: list[str]  # From reality (where it IS running)

    @property
    def is_multi_host(self) -> bool:
        """Check if this is a multi-host stack."""
        return len(self.configured_hosts) > 1

    @property
    def stray_hosts(self) -> list[str]:
        """Hosts where stack is running but shouldn't be."""
        return [h for h in self.running_hosts if h not in self.configured_hosts]

    @property
    def missing_hosts(self) -> list[str]:
        """Hosts where stack should be running but isn't."""
        return [h for h in self.configured_hosts if h not in self.running_hosts]

    @property
    def is_stray(self) -> bool:
        """Stack is running on unauthorized host(s)."""
        return len(self.stray_hosts) > 0

    @property
    def is_duplicate(self) -> bool:
        """Single-host stack running on multiple hosts."""
        return not self.is_multi_host and len(self.running_hosts) > 1


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


async def _stop_stacks_on_hosts(
    cfg: Config,
    stacks_to_hosts: dict[str, list[str]],
    label: str = "",
) -> list[CommandResult]:
    """Stop stacks on specific hosts.

    Shared helper for stop_orphaned_stacks and stop_stray_stacks.

    Args:
        cfg: Config object.
        stacks_to_hosts: Dict mapping stack name to list of hosts to stop on.
        label: Optional label for success message (e.g., "stray", "orphaned").

    Returns:
        List of CommandResults for each stack@host.

    """
    if not stacks_to_hosts:
        return []

    results: list[CommandResult] = []
    tasks: list[tuple[str, str, asyncio.Task[CommandResult]]] = []
    suffix = f" ({label})" if label else ""

    for stack, hosts in stacks_to_hosts.items():
        for host in hosts:
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

    for stack, host, task in tasks:
        try:
            result = await task
            results.append(result)
            if result.success:
                print_success(f"{stack}@{host}: stopped{suffix}")
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

    normalized: dict[str, list[str]] = {
        stack: (hosts if isinstance(hosts, list) else [hosts]) for stack, hosts in orphaned.items()
    }

    results = await _stop_stacks_on_hosts(cfg, normalized)

    # Remove from state only for stacks where ALL hosts succeeded
    for stack in normalized:
        all_succeeded = all(
            r.success for r in results if r.stack.startswith(f"{stack}@") or r.stack == stack
        )
        if all_succeeded:
            remove_stack(cfg, stack)

    return results


async def stop_stray_stacks(
    cfg: Config,
    strays: dict[str, list[str]],
) -> list[CommandResult]:
    """Stop stacks running on unauthorized hosts.

    Args:
        cfg: Config object.
        strays: Dict mapping stack name to list of stray hosts.

    Returns:
        List of CommandResults for each stack@host stopped.

    """
    return await _stop_stacks_on_hosts(cfg, strays, label="stray")


def build_discovery_results(
    cfg: Config,
    running_on_host: dict[str, set[str]],
    stacks: list[str] | None = None,
) -> tuple[dict[str, str | list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """Build discovery results from per-host running stacks.

    Takes the raw data of which stacks are running on which hosts and
    categorizes them into discovered (running correctly), strays (wrong host),
    and duplicates (single-host stack on multiple hosts).

    Args:
        cfg: Config object.
        running_on_host: Dict mapping host -> set of running stack names.
        stacks: Optional list of stacks to check. Defaults to all configured stacks.

    Returns:
        Tuple of (discovered, strays, duplicates):
        - discovered: stack -> host(s) where running correctly
        - strays: stack -> list of unauthorized hosts
        - duplicates: stack -> list of all hosts (for single-host stacks on multiple)

    """
    stack_list = stacks if stacks is not None else list(cfg.stacks)
    all_hosts = list(running_on_host.keys())

    # Build StackDiscoveryResult for each stack
    results: list[StackDiscoveryResult] = [
        StackDiscoveryResult(
            stack=stack,
            configured_hosts=cfg.get_hosts(stack),
            running_hosts=[h for h in all_hosts if stack in running_on_host[h]],
        )
        for stack in stack_list
    ]

    discovered: dict[str, str | list[str]] = {}
    strays: dict[str, list[str]] = {}
    duplicates: dict[str, list[str]] = {}

    for result in results:
        correct_hosts = [h for h in result.running_hosts if h in result.configured_hosts]
        if correct_hosts:
            if result.is_multi_host:
                discovered[result.stack] = correct_hosts
            else:
                discovered[result.stack] = correct_hosts[0]

        if result.is_stray:
            strays[result.stack] = result.stray_hosts

        if result.is_duplicate:
            duplicates[result.stack] = result.running_hosts

    return discovered, strays, duplicates
