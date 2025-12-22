"""Lifecycle commands: up, down, pull, restart, update, apply."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from compose_farm.config import Config

from compose_farm.cli.app import app
from compose_farm.cli.common import (
    AllOption,
    ConfigOption,
    HostOption,
    ServiceOption,
    StacksArg,
    format_host,
    get_stacks,
    load_config_or_exit,
    maybe_regenerate_traefik,
    report_results,
    run_async,
    validate_host_for_stack,
    validate_stacks,
)
from compose_farm.cli.management import _discover_stacks_full
from compose_farm.console import MSG_DRY_RUN, console, print_error, print_success
from compose_farm.executor import run_compose_on_host, run_on_stacks, run_sequential_on_stacks
from compose_farm.operations import (
    stop_orphaned_stacks,
    stop_stray_stacks,
    up_stacks,
)
from compose_farm.state import (
    get_orphaned_stacks,
    get_stack_host,
    get_stacks_needing_migration,
    get_stacks_not_in_state,
    remove_stack,
)


@app.command(rich_help_panel="Lifecycle")
def up(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    host: HostOption = None,
    service: ServiceOption = None,
    config: ConfigOption = None,
) -> None:
    """Start stacks (docker compose up -d). Auto-migrates if host changed."""
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config, host=host)
    if service:
        if len(stack_list) != 1:
            print_error("--service requires exactly one stack")
            raise typer.Exit(1)
        # For service-level up, use run_on_stacks directly (no migration logic)
        results = run_async(run_on_stacks(cfg, stack_list, f"up -d {service}", raw=True))
    else:
        results = run_async(up_stacks(cfg, stack_list, raw=True))
    maybe_regenerate_traefik(cfg, results)
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def down(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    orphaned: Annotated[
        bool,
        typer.Option("--orphaned", help="Stop orphaned stacks (in state but removed from config)"),
    ] = False,
    host: HostOption = None,
    config: ConfigOption = None,
) -> None:
    """Stop stacks (docker compose down)."""
    # Handle --orphaned flag (mutually exclusive with other selection methods)
    if orphaned:
        if stacks or all_stacks or host:
            print_error(
                "Cannot combine [bold]--orphaned[/] with stacks, [bold]--all[/], or [bold]--host[/]"
            )
            raise typer.Exit(1)

        cfg = load_config_or_exit(config)
        orphaned_stacks = get_orphaned_stacks(cfg)

        if not orphaned_stacks:
            print_success("No orphaned stacks to stop")
            return

        console.print(
            f"[yellow]Stopping {len(orphaned_stacks)} orphaned stack(s):[/] "
            f"{', '.join(orphaned_stacks.keys())}"
        )
        results = run_async(stop_orphaned_stacks(cfg))
        report_results(results)
        return

    stack_list, cfg = get_stacks(stacks or [], all_stacks, config, host=host)
    raw = len(stack_list) == 1
    results = run_async(run_on_stacks(cfg, stack_list, "down", raw=raw))

    # Remove from state on success
    # For multi-host stacks, result.stack is "stack@host", extract base name
    removed_stacks: set[str] = set()
    for result in results:
        if result.success:
            base_stack = result.stack.split("@")[0]
            if base_stack not in removed_stacks:
                remove_stack(cfg, base_stack)
                removed_stacks.add(base_stack)

    maybe_regenerate_traefik(cfg, results)
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def stop(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    service: ServiceOption = None,
    config: ConfigOption = None,
) -> None:
    """Stop services without removing containers (docker compose stop)."""
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config)
    if service and len(stack_list) != 1:
        print_error("--service requires exactly one stack")
        raise typer.Exit(1)
    cmd = f"stop {service}" if service else "stop"
    raw = len(stack_list) == 1
    results = run_async(run_on_stacks(cfg, stack_list, cmd, raw=raw))
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def pull(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    service: ServiceOption = None,
    config: ConfigOption = None,
) -> None:
    """Pull latest images (docker compose pull)."""
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config)
    if service and len(stack_list) != 1:
        print_error("--service requires exactly one stack")
        raise typer.Exit(1)
    cmd = f"pull --ignore-buildable {service}" if service else "pull --ignore-buildable"
    raw = len(stack_list) == 1
    results = run_async(run_on_stacks(cfg, stack_list, cmd, raw=raw))
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def restart(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    service: ServiceOption = None,
    config: ConfigOption = None,
) -> None:
    """Restart stacks (down + up). With --service, restarts just that service."""
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config)
    if service:
        if len(stack_list) != 1:
            print_error("--service requires exactly one stack")
            raise typer.Exit(1)
        # For service-level restart, use docker compose restart (more efficient)
        raw = True
        results = run_async(run_on_stacks(cfg, stack_list, f"restart {service}", raw=raw))
    else:
        raw = len(stack_list) == 1
        results = run_async(run_sequential_on_stacks(cfg, stack_list, ["down", "up -d"], raw=raw))
    maybe_regenerate_traefik(cfg, results)
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def update(
    stacks: StacksArg = None,
    all_stacks: AllOption = False,
    service: ServiceOption = None,
    config: ConfigOption = None,
) -> None:
    """Update stacks (pull + build + down + up). With --service, updates just that service."""
    stack_list, cfg = get_stacks(stacks or [], all_stacks, config)
    if service:
        if len(stack_list) != 1:
            print_error("--service requires exactly one stack")
            raise typer.Exit(1)
        # For service-level update: pull + build + stop + up (stop instead of down)
        raw = True
        results = run_async(
            run_sequential_on_stacks(
                cfg,
                stack_list,
                [
                    f"pull --ignore-buildable {service}",
                    f"build {service}",
                    f"stop {service}",
                    f"up -d {service}",
                ],
                raw=raw,
            )
        )
    else:
        raw = len(stack_list) == 1
        results = run_async(
            run_sequential_on_stacks(
                cfg, stack_list, ["pull --ignore-buildable", "build", "down", "up -d"], raw=raw
            )
        )
    maybe_regenerate_traefik(cfg, results)
    report_results(results)


def _discover_strays(cfg: Config) -> dict[str, list[str]]:
    """Discover stacks running on unauthorized hosts by scanning all hosts."""
    _, strays, duplicates = _discover_stacks_full(cfg)

    # Merge duplicates into strays (for single-host stacks on multiple hosts,
    # keep correct host and stop others)
    for stack, running_hosts in duplicates.items():
        configured = cfg.get_hosts(stack)[0]
        stray_hosts = [h for h in running_hosts if h != configured]
        if stray_hosts:
            strays[stack] = stray_hosts

    return strays


@app.command(rich_help_panel="Lifecycle")
def apply(  # noqa: C901, PLR0912, PLR0915 (multi-phase reconciliation needs these branches)
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would change without executing"),
    ] = False,
    no_orphans: Annotated[
        bool,
        typer.Option("--no-orphans", help="Only migrate, don't stop orphaned stacks"),
    ] = False,
    no_strays: Annotated[
        bool,
        typer.Option("--no-strays", help="Don't stop stray stacks (running on wrong host)"),
    ] = False,
    full: Annotated[
        bool,
        typer.Option("--full", "-f", help="Also run up on all stacks to apply config changes"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Make reality match config (start, migrate, stop strays/orphans as needed).

    This is the "reconcile" command that ensures running stacks match your
    config file. It will:

    1. Stop orphaned stacks (in state but removed from config)
    2. Stop stray stacks (running on unauthorized hosts)
    3. Migrate stacks on wrong host (host in state ≠ host in config)
    4. Start missing stacks (in config but not in state)

    Use --dry-run to preview changes before applying.
    Use --no-orphans to skip stopping orphaned stacks.
    Use --no-strays to skip stopping stray stacks.
    Use --full to also run 'up' on all stacks (picks up compose/env changes).
    """
    cfg = load_config_or_exit(config)
    orphaned = get_orphaned_stacks(cfg)
    migrations = get_stacks_needing_migration(cfg)
    missing = get_stacks_not_in_state(cfg)

    strays: dict[str, list[str]] = {}
    if not no_strays:
        console.print("[dim]Scanning hosts for stray containers...[/]")
        strays = _discover_strays(cfg)

    # For --full: refresh all stacks not already being started/migrated
    handled = set(migrations) | set(missing)
    to_refresh = [stack for stack in cfg.stacks if stack not in handled] if full else []

    has_orphans = bool(orphaned) and not no_orphans
    has_strays = bool(strays)
    has_migrations = bool(migrations)
    has_missing = bool(missing)
    has_refresh = bool(to_refresh)

    if (
        not has_orphans
        and not has_strays
        and not has_migrations
        and not has_missing
        and not has_refresh
    ):
        print_success("Nothing to apply - reality matches config")
        return

    # Report what will be done
    if has_orphans:
        console.print(f"[yellow]Orphaned stacks to stop ({len(orphaned)}):[/]")
        for svc, hosts in orphaned.items():
            console.print(f"  [cyan]{svc}[/] on [magenta]{format_host(hosts)}[/]")
    if has_strays:
        console.print(f"[red]Stray stacks to stop ({len(strays)}):[/]")
        for stack, hosts in strays.items():
            configured = cfg.get_hosts(stack)
            console.print(
                f"  [cyan]{stack}[/] on [magenta]{', '.join(hosts)}[/] "
                f"[dim](should be on {', '.join(configured)})[/]"
            )
    if has_migrations:
        console.print(f"[cyan]Stacks to migrate ({len(migrations)}):[/]")
        for stack in migrations:
            current = get_stack_host(cfg, stack)
            target = cfg.get_hosts(stack)[0]
            console.print(f"  [cyan]{stack}[/]: [magenta]{current}[/] → [magenta]{target}[/]")
    if has_missing:
        console.print(f"[green]Stacks to start ({len(missing)}):[/]")
        for stack in missing:
            console.print(f"  [cyan]{stack}[/] on [magenta]{format_host(cfg.get_hosts(stack))}[/]")
    if has_refresh:
        console.print(f"[blue]Stacks to refresh ({len(to_refresh)}):[/]")
        for stack in to_refresh:
            console.print(f"  [cyan]{stack}[/] on [magenta]{format_host(cfg.get_hosts(stack))}[/]")

    if dry_run:
        console.print(f"\n{MSG_DRY_RUN}")
        return

    # Execute changes
    console.print()
    all_results = []

    # 1. Stop orphaned stacks first
    if has_orphans:
        console.print("[yellow]Stopping orphaned stacks...[/]")
        all_results.extend(run_async(stop_orphaned_stacks(cfg)))

    # 2. Stop stray stacks (running on unauthorized hosts)
    if has_strays:
        console.print("[red]Stopping stray stacks...[/]")
        all_results.extend(run_async(stop_stray_stacks(cfg, strays)))

    # 3. Migrate stacks on wrong host
    if has_migrations:
        console.print("[cyan]Migrating stacks...[/]")
        migrate_results = run_async(up_stacks(cfg, migrations, raw=True))
        all_results.extend(migrate_results)
        maybe_regenerate_traefik(cfg, migrate_results)

    # 4. Start missing stacks (reuse up_stacks which handles state updates)
    if has_missing:
        console.print("[green]Starting missing stacks...[/]")
        start_results = run_async(up_stacks(cfg, missing, raw=True))
        all_results.extend(start_results)
        maybe_regenerate_traefik(cfg, start_results)

    # 5. Refresh remaining stacks (--full: run up to apply config changes)
    if has_refresh:
        console.print("[blue]Refreshing stacks...[/]")
        refresh_results = run_async(up_stacks(cfg, to_refresh, raw=True))
        all_results.extend(refresh_results)
        maybe_regenerate_traefik(cfg, refresh_results)

    report_results(all_results)


@app.command(
    rich_help_panel="Lifecycle",
    context_settings={"allow_interspersed_args": False},
)
def compose(
    stack: Annotated[str, typer.Argument(help="Stack to operate on (use '.' for current dir)")],
    command: Annotated[str, typer.Argument(help="Docker compose command")],
    args: Annotated[list[str] | None, typer.Argument(help="Additional arguments")] = None,
    host: HostOption = None,
    config: ConfigOption = None,
) -> None:
    """Run any docker compose command on a stack.

    Passthrough to docker compose for commands not wrapped by cf.
    Options after COMMAND are passed to docker compose, not cf.

    Examples:
      cf compose mystack --help        - show docker compose help
      cf compose mystack top           - view running processes
      cf compose mystack images        - list images
      cf compose mystack exec web bash - interactive shell
      cf compose mystack config        - view parsed config

    """
    cfg = load_config_or_exit(config)

    # Resolve "." to current directory name
    resolved_stack = Path.cwd().name if stack == "." else stack
    validate_stacks(cfg, [resolved_stack])

    # Handle multi-host stacks
    hosts = cfg.get_hosts(resolved_stack)
    if len(hosts) > 1:
        if host is None:
            print_error(
                f"Stack [cyan]{resolved_stack}[/] runs on multiple hosts: {', '.join(hosts)}\n"
                f"Use [bold]--host[/] to specify which host"
            )
            raise typer.Exit(1)
        validate_host_for_stack(cfg, resolved_stack, host)
        target_host = host
    else:
        target_host = hosts[0]

    # Build the full compose command
    full_cmd = command
    if args:
        full_cmd += " " + " ".join(args)

    # Run with raw=True for proper TTY handling (progress bars, interactive)
    result = run_async(run_compose_on_host(cfg, resolved_stack, target_host, full_cmd, raw=True))
    print()  # Ensure newline after raw output

    if not result.success:
        raise typer.Exit(result.exit_code)


# Alias: cf a = cf apply
app.command("a", hidden=True)(apply)
