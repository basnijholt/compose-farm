"""Management commands: sync, check, init-network, traefik-file."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated

import typer

from compose_farm.cli.app import app
from compose_farm.cli.common import (
    _MISSING_PATH_PREVIEW_LIMIT,
    AllOption,
    ConfigOption,
    LogPathOption,
    StacksArg,
    format_host,
    get_stacks,
    load_config_or_exit,
    run_async,
    run_parallel_with_progress,
    validate_hosts,
    validate_stacks,
)

if TYPE_CHECKING:
    from compose_farm.config import Config

from compose_farm.console import (
    MSG_DRY_RUN,
    console,
    print_error,
    print_success,
    print_warning,
)
from compose_farm.executor import (
    CommandResult,
    get_running_stacks_on_host,
    is_local,
    run_command,
)
from compose_farm.logs import (
    DEFAULT_LOG_PATH,
    SnapshotEntry,
    collect_stacks_entries_on_host,
    isoformat,
    load_existing_entries,
    merge_entries,
    write_toml,
)
from compose_farm.operations import (
    build_discovery_results,
    check_host_compatibility,
    check_stack_requirements,
)
from compose_farm.state import get_orphaned_stacks, load_state, save_state

# --- Sync helpers ---


def _snapshot_stacks(
    cfg: Config,
    discovered: dict[str, str | list[str]],
    log_path: Path | None,
) -> Path:
    """Capture image digests using batched SSH calls (1 per host).

    Args:
        cfg: Configuration
        discovered: Dict mapping stack -> host(s) where it's running
        log_path: Optional path to write the log file

    Returns:
        Path to the written log file.

    """
    effective_log_path = log_path or DEFAULT_LOG_PATH
    now_dt = datetime.now(UTC)
    now_iso = isoformat(now_dt)

    # Group stacks by host for batched SSH calls
    stacks_by_host: dict[str, set[str]] = {}
    for stack, hosts in discovered.items():
        # Use first host for multi-host stacks (they use the same images)
        host = hosts[0] if isinstance(hosts, list) else hosts
        stacks_by_host.setdefault(host, set()).add(stack)

    # Collect entries with 1 SSH call per host (with progress bar)
    async def collect_on_host(host: str) -> tuple[str, list[SnapshotEntry]]:
        entries = await collect_stacks_entries_on_host(cfg, host, stacks_by_host[host], now=now_dt)
        return host, entries

    results = run_parallel_with_progress("Capturing", list(stacks_by_host.keys()), collect_on_host)
    snapshot_entries = [entry for _, entries in results for entry in entries]

    if not snapshot_entries:
        msg = "No image digests were captured"
        raise RuntimeError(msg)

    existing_entries = load_existing_entries(effective_log_path)
    merged_entries = merge_entries(existing_entries, snapshot_entries, now_iso=now_iso)
    meta = {"generated_at": now_iso, "compose_dir": str(cfg.compose_dir)}
    write_toml(effective_log_path, meta=meta, entries=merged_entries)
    return effective_log_path


def _merge_state(
    current_state: dict[str, str | list[str]],
    discovered: dict[str, str | list[str]],
    removed: list[str],
) -> dict[str, str | list[str]]:
    """Merge discovered stacks into existing state for partial refresh."""
    new_state = {**current_state, **discovered}
    for svc in removed:
        new_state.pop(svc, None)
    return new_state


def _report_sync_changes(
    added: list[str],
    removed: list[str],
    changed: list[tuple[str, str | list[str], str | list[str]]],
    discovered: dict[str, str | list[str]],
    current_state: dict[str, str | list[str]],
) -> None:
    """Report sync changes to the user."""
    if added:
        console.print(f"\nNew stacks found ({len(added)}):")
        for stack in sorted(added):
            host_str = format_host(discovered[stack])
            console.print(f"  [green]+[/] [cyan]{stack}[/] on [magenta]{host_str}[/]")

    if changed:
        console.print(f"\nStacks on different hosts ({len(changed)}):")
        for stack, old_host, new_host in sorted(changed):
            old_str = format_host(old_host)
            new_str = format_host(new_host)
            console.print(
                f"  [yellow]~[/] [cyan]{stack}[/]: [magenta]{old_str}[/] → [magenta]{new_str}[/]"
            )

    if removed:
        console.print(f"\nStacks no longer running ({len(removed)}):")
        for stack in sorted(removed):
            host_str = format_host(current_state[stack])
            console.print(f"  [red]-[/] [cyan]{stack}[/] (was on [magenta]{host_str}[/])")


def _discover_stacks_full(
    cfg: Config,
    stacks: list[str] | None = None,
) -> tuple[dict[str, str | list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """Discover running stacks with full host scanning for stray detection.

    Queries each host once for all running stacks (with progress bar),
    then delegates to build_discovery_results for categorization.
    """
    all_hosts = list(cfg.hosts.keys())

    # Query each host for running stacks (with progress bar)
    async def get_stacks_on_host(host: str) -> tuple[str, set[str]]:
        running = await get_running_stacks_on_host(cfg, host)
        return host, running

    host_results = run_parallel_with_progress("Discovering", all_hosts, get_stacks_on_host)
    running_on_host: dict[str, set[str]] = dict(host_results)

    return build_discovery_results(cfg, running_on_host, stacks)


def _report_stray_stacks(
    strays: dict[str, list[str]],
    cfg: Config,
) -> None:
    """Report stacks running on unauthorized hosts."""
    if strays:
        console.print(f"\n[red]Stray stacks[/] (running on wrong host, {len(strays)}):")
        console.print("[dim]Run [bold]cf apply[/bold] to stop them.[/]")
        for stack in sorted(strays):
            stray_hosts = strays[stack]
            configured = cfg.get_hosts(stack)
            console.print(
                f"  [red]![/] [cyan]{stack}[/] on [magenta]{', '.join(stray_hosts)}[/] "
                f"[dim](should be on {', '.join(configured)})[/]"
            )


def _report_duplicate_stacks(duplicates: dict[str, list[str]], cfg: Config) -> None:
    """Report single-host stacks running on multiple hosts."""
    if duplicates:
        console.print(
            f"\n[yellow]Duplicate stacks[/] (running on multiple hosts, {len(duplicates)}):"
        )
        console.print("[dim]Run [bold]cf apply[/bold] to stop extras.[/]")
        for stack in sorted(duplicates):
            hosts = duplicates[stack]
            configured = cfg.get_hosts(stack)[0]
            console.print(
                f"  [yellow]![/] [cyan]{stack}[/] on [magenta]{', '.join(hosts)}[/] "
                f"[dim](should only be on {configured})[/]"
            )


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
        try:
            result = await asyncio.wait_for(
                run_command(host, "echo ok", host_name, stream=False),
                timeout=5.0,
            )
            return host_name, result.success
        except TimeoutError:
            return host_name, False

    results = run_parallel_with_progress(
        "Checking SSH connectivity",
        remote_hosts,
        check_host,
    )
    return [host for host, success in results if not success]


def _check_stack_requirements(
    cfg: Config,
    stacks: list[str],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Check mounts, networks, and devices for all stacks with a progress bar.

    Returns (mount_errors, network_errors, device_errors) where each is a list of
    (stack, host, missing_item) tuples.
    """

    async def check_stack(
        stack: str,
    ) -> tuple[
        str,
        list[tuple[str, str, str]],
        list[tuple[str, str, str]],
        list[tuple[str, str, str]],
    ]:
        """Check requirements for a single stack on all its hosts."""
        host_names = cfg.get_hosts(stack)
        mount_errors: list[tuple[str, str, str]] = []
        network_errors: list[tuple[str, str, str]] = []
        device_errors: list[tuple[str, str, str]] = []

        for host_name in host_names:
            missing_paths, missing_nets, missing_devs = await check_stack_requirements(
                cfg, stack, host_name
            )
            mount_errors.extend((stack, host_name, p) for p in missing_paths)
            network_errors.extend((stack, host_name, n) for n in missing_nets)
            device_errors.extend((stack, host_name, d) for d in missing_devs)

        return stack, mount_errors, network_errors, device_errors

    results = run_parallel_with_progress(
        "Checking requirements",
        stacks,
        check_stack,
    )

    all_mount_errors: list[tuple[str, str, str]] = []
    all_network_errors: list[tuple[str, str, str]] = []
    all_device_errors: list[tuple[str, str, str]] = []
    for _, mount_errs, net_errs, dev_errs in results:
        all_mount_errors.extend(mount_errs)
        all_network_errors.extend(net_errs)
        all_device_errors.extend(dev_errs)

    return all_mount_errors, all_network_errors, all_device_errors


def _report_config_status(cfg: Config) -> bool:
    """Check and report config vs disk status. Returns True if errors found."""
    configured = set(cfg.stacks.keys())
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
        print_success("Config matches disk")

    return bool(missing_from_disk)


def _report_orphaned_stacks(cfg: Config) -> bool:
    """Check for stacks in state but not in config. Returns True if orphans found."""
    orphaned = get_orphaned_stacks(cfg)

    if orphaned:
        console.print("\n[yellow]Orphaned stacks[/] (in state but not in config):")
        console.print(
            "[dim]Run [bold]cf apply[/bold] to stop them, or [bold]cf down --orphaned[/bold] for just orphans.[/]"
        )
        for name, hosts in sorted(orphaned.items()):
            console.print(f"  [yellow]![/] [cyan]{name}[/] on [magenta]{format_host(hosts)}[/]")
        return True

    return False


def _report_traefik_status(cfg: Config, stacks: list[str]) -> None:
    """Check and report traefik label status."""
    from compose_farm.traefik import generate_traefik_config  # noqa: PLC0415

    try:
        _, warnings = generate_traefik_config(cfg, stacks, check_all=True)
    except (FileNotFoundError, ValueError):
        return

    if warnings:
        console.print(f"\n[yellow]Traefik issues[/] ({len(warnings)}):")
        for warning in warnings:
            print_warning(warning)
    else:
        print_success("Traefik labels valid")


def _report_requirement_errors(errors: list[tuple[str, str, str]], category: str) -> None:
    """Report requirement errors (mounts, networks, devices) grouped by stack."""
    by_stack: dict[str, list[tuple[str, str]]] = {}
    for stack, host, item in errors:
        by_stack.setdefault(stack, []).append((host, item))

    console.print(f"[red]Missing {category}[/] ({len(errors)}):")
    for stack, items in sorted(by_stack.items()):
        host = items[0][0]
        missing = [i for _, i in items]
        console.print(f"  [cyan]{stack}[/] on [magenta]{host}[/]:")
        for item in missing:
            console.print(f"    [red]✗[/] {item}")


def _report_ssh_status(unreachable_hosts: list[str]) -> bool:
    """Report SSH connectivity status. Returns True if there are errors."""
    if unreachable_hosts:
        console.print(f"[red]Unreachable hosts[/] ({len(unreachable_hosts)}):")
        for host in sorted(unreachable_hosts):
            print_error(f"[magenta]{host}[/]")
        return True
    print_success("All hosts reachable")
    return False


def _report_host_compatibility(
    compat: dict[str, tuple[int, int, list[str]]],
    assigned_hosts: list[str],
) -> None:
    """Report host compatibility for a stack."""
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
    mount_errors, network_errors, device_errors = _check_stack_requirements(cfg, svc_list)

    if mount_errors:
        _report_requirement_errors(mount_errors, "mounts")
        has_errors = True
    if network_errors:
        _report_requirement_errors(network_errors, "networks")
        has_errors = True
    if device_errors:
        _report_requirement_errors(device_errors, "devices")
        has_errors = True
    if not mount_errors and not network_errors and not device_errors:
        print_success("All mounts, networks, and devices exist")

    if show_host_compat:
        for stack in svc_list:
            console.print(f"\n[bold]Host compatibility for[/] [cyan]{stack}[/]:")
            compat = run_async(check_host_compatibility(cfg, stack))
            assigned_hosts = cfg.get_hosts(stack)
            _report_host_compatibility(compat, assigned_hosts)

    return has_errors


# Default network settings for cross-host Docker networking
_DEFAULT_NETWORK_NAME = "mynetwork"
_DEFAULT_NETWORK_SUBNET = "172.20.0.0/16"
_DEFAULT_NETWORK_GATEWAY = "172.20.0.1"


@app.command("traefik-file", rich_help_panel="Configuration")
def traefik_file(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
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
    from compose_farm.traefik import (  # noqa: PLC0415
        generate_traefik_config,
        render_traefik_config,
    )

    stack_list, cfg = get_stacks(stacks or [], all_stacks, config)
    try:
        dynamic, warnings = generate_traefik_config(cfg, stack_list)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc))
        raise typer.Exit(1) from exc

    rendered = render_traefik_config(dynamic)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        print_success(f"Traefik config written to {output}")
    else:
        console.print(rendered)

    for warning in warnings:
        print_warning(warning)


@app.command(rich_help_panel="Configuration")
def refresh(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    config: ConfigOption = None,
    log_path: LogPathOption = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would change without writing"),
    ] = False,
) -> None:
    """Update local state from running stacks.

    Discovers which stacks are running on which hosts, updates the state
    file, and captures image digests. This is a read operation - it updates
    your local state to match reality, not the other way around.

    Without arguments: refreshes all stacks (same as --all).
    With stack names: refreshes only those stacks.

    Use 'cf apply' to make reality match your config (stop orphans, migrate).
    """
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config, default_all=True)

    # Partial refresh merges with existing state; full refresh replaces it
    # Partial = specific stacks provided (not --all, not default)
    partial_refresh = bool(stacks) and not all_stacks

    current_state = load_state(cfg)

    discovered, strays, duplicates = _discover_stacks_full(cfg, stack_list)

    # Calculate changes (only for the stacks we're refreshing)
    added = [s for s in discovered if s not in current_state]
    # Only mark as "removed" if we're doing a full refresh
    if partial_refresh:
        # In partial refresh, a stack not running is just "not found"
        removed = [s for s in stack_list if s in current_state and s not in discovered]
    else:
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
        print_success("State is already in sync.")

    _report_stray_stacks(strays, cfg)
    _report_duplicate_stacks(duplicates, cfg)

    if dry_run:
        console.print(f"\n{MSG_DRY_RUN}")
        return

    # Update state file
    if state_changed:
        new_state = (
            _merge_state(current_state, discovered, removed) if partial_refresh else discovered
        )
        save_state(cfg, new_state)
        print_success(f"State updated: {len(new_state)} stacks tracked.")

    # Capture image digests for running stacks (1 SSH call per host)
    if discovered:
        try:
            path = _snapshot_stacks(cfg, discovered, log_path)
            print_success(f"Digests written to {path}")
        except RuntimeError as exc:
            print_warning(str(exc))


@app.command(rich_help_panel="Configuration")
def check(
    stacks: StacksArg = None,
    local: Annotated[
        bool,
        typer.Option("--local", help="Skip SSH-based checks (faster)"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Validate configuration, traefik labels, mounts, and networks.

    Without arguments: validates all stacks against configured hosts.
    With stack arguments: validates specific stacks and shows host compatibility.

    Use --local to skip SSH-based checks for faster validation.
    """
    cfg = load_config_or_exit(config)

    # Determine which stacks to check and whether to show host compatibility
    if stacks:
        stack_list = list(stacks)
        validate_stacks(cfg, stack_list)
        show_host_compat = True
    else:
        stack_list = list(cfg.stacks.keys())
        show_host_compat = False

    # Run checks
    has_errors = _report_config_status(cfg)
    _report_traefik_status(cfg, stack_list)

    if not local and _run_remote_checks(cfg, stack_list, show_host_compat=show_host_compat):
        has_errors = True

    # Check for orphaned stacks (in state but removed from config)
    if _report_orphaned_stacks(cfg):
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

    Creates an external Docker network that stacks can use for cross-host
    communication. Uses the same subnet/gateway on all hosts to ensure
    consistent networking.
    """
    cfg = load_config_or_exit(config)

    target_hosts = list(hosts) if hosts else list(cfg.hosts.keys())
    validate_hosts(cfg, target_hosts)

    async def create_network_on_host(host_name: str) -> CommandResult:
        host = cfg.hosts[host_name]
        # Check if network already exists
        check_cmd = f"docker network inspect '{network}' >/dev/null 2>&1"
        check_result = await run_command(host, check_cmd, host_name, stream=False)

        if check_result.success:
            console.print(f"[cyan]\\[{host_name}][/] Network '{network}' already exists")
            return CommandResult(stack=host_name, exit_code=0, success=True)

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
            print_error(
                f"[cyan]\\[{host_name}][/] Failed to create network: {result.stderr.strip()}"
            )

        return result

    async def run_all() -> list[CommandResult]:
        return await asyncio.gather(*[create_network_on_host(h) for h in target_hosts])

    results = run_async(run_all())
    failed = [r for r in results if not r.success]
    if failed:
        raise typer.Exit(1)
