"""Lifecycle commands: up, down, pull, restart, update, apply."""

from __future__ import annotations

from typing import Annotated

import typer

from compose_farm.cli.app import app
from compose_farm.cli.common import (
    AllOption,
    ConfigOption,
    HostOption,
    ServicesArg,
    format_host,
    get_services,
    load_config_or_exit,
    maybe_regenerate_traefik,
    report_results,
    run_async,
)
from compose_farm.console import MSG_DRY_RUN, console, print_error, print_success
from compose_farm.executor import run_on_services, run_sequential_on_services
from compose_farm.operations import stop_orphaned_services, up_services
from compose_farm.state import (
    get_orphaned_services,
    get_service_host,
    get_services_needing_migration,
    get_services_not_in_state,
    remove_service,
)


@app.command(rich_help_panel="Lifecycle")
def up(
    services: ServicesArg = None,
    all_services: AllOption = False,
    host: HostOption = None,
    config: ConfigOption = None,
) -> None:
    """Start services (docker compose up -d). Auto-migrates if host changed."""
    svc_list, cfg = get_services(services or [], all_services, config, host=host)
    results = run_async(up_services(cfg, svc_list, raw=True))
    maybe_regenerate_traefik(cfg, results)
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def down(
    services: ServicesArg = None,
    all_services: AllOption = False,
    orphaned: Annotated[
        bool,
        typer.Option(
            "--orphaned", help="Stop orphaned services (in state but removed from config)"
        ),
    ] = False,
    host: HostOption = None,
    config: ConfigOption = None,
) -> None:
    """Stop services (docker compose down)."""
    # Handle --orphaned flag (mutually exclusive with other selection methods)
    if orphaned:
        if services or all_services or host:
            print_error(
                "Cannot combine [bold]--orphaned[/] with services, [bold]--all[/], or [bold]--host[/]"
            )
            raise typer.Exit(1)

        cfg = load_config_or_exit(config)
        orphaned_services = get_orphaned_services(cfg)

        if not orphaned_services:
            print_success("No orphaned services to stop")
            return

        console.print(
            f"[yellow]Stopping {len(orphaned_services)} orphaned service(s):[/] "
            f"{', '.join(orphaned_services.keys())}"
        )
        results = run_async(stop_orphaned_services(cfg))
        report_results(results)
        return

    svc_list, cfg = get_services(services or [], all_services, config, host=host)
    raw = len(svc_list) == 1
    results = run_async(run_on_services(cfg, svc_list, "down", raw=raw))

    # Remove from state on success
    # For multi-host services, result.service is "svc@host", extract base name
    removed_services: set[str] = set()
    for result in results:
        if result.success:
            base_service = result.service.split("@")[0]
            if base_service not in removed_services:
                remove_service(cfg, base_service)
                removed_services.add(base_service)

    maybe_regenerate_traefik(cfg, results)
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def pull(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Pull latest images (docker compose pull)."""
    svc_list, cfg = get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = run_async(run_on_services(cfg, svc_list, "pull", raw=raw))
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def restart(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Restart services (down + up)."""
    svc_list, cfg = get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = run_async(run_sequential_on_services(cfg, svc_list, ["down", "up -d"], raw=raw))
    maybe_regenerate_traefik(cfg, results)
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def update(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Update services (pull + build + down + up)."""
    svc_list, cfg = get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = run_async(
        run_sequential_on_services(
            cfg, svc_list, ["pull --ignore-buildable", "build", "down", "up -d"], raw=raw
        )
    )
    maybe_regenerate_traefik(cfg, results)
    report_results(results)


@app.command(rich_help_panel="Lifecycle")
def apply(  # noqa: PLR0912 (multi-phase reconciliation needs these branches)
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would change without executing"),
    ] = False,
    no_orphans: Annotated[
        bool,
        typer.Option("--no-orphans", help="Only migrate, don't stop orphaned services"),
    ] = False,
    full: Annotated[
        bool,
        typer.Option("--full", "-f", help="Also run up on all services to apply config changes"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Make reality match config (start, migrate, stop as needed).

    This is the "reconcile" command that ensures running services match your
    config file. It will:

    1. Stop orphaned services (in state but removed from config)
    2. Migrate services on wrong host (host in state ≠ host in config)
    3. Start missing services (in config but not in state)

    Use --dry-run to preview changes before applying.
    Use --no-orphans to only migrate/start without stopping orphaned services.
    Use --full to also run 'up' on all services (picks up compose/env changes).
    """
    cfg = load_config_or_exit(config)
    orphaned = get_orphaned_services(cfg)
    migrations = get_services_needing_migration(cfg)
    missing = get_services_not_in_state(cfg)

    # For --full: refresh all services not already being started/migrated
    handled = set(migrations) | set(missing)
    to_refresh = [svc for svc in cfg.services if svc not in handled] if full else []

    has_orphans = bool(orphaned) and not no_orphans
    has_migrations = bool(migrations)
    has_missing = bool(missing)
    has_refresh = bool(to_refresh)

    if not has_orphans and not has_migrations and not has_missing and not has_refresh:
        print_success("Nothing to apply - reality matches config")
        return

    # Report what will be done
    if has_orphans:
        console.print(f"[yellow]Orphaned services to stop ({len(orphaned)}):[/]")
        for svc, hosts in orphaned.items():
            console.print(f"  [cyan]{svc}[/] on [magenta]{format_host(hosts)}[/]")
    if has_migrations:
        console.print(f"[cyan]Services to migrate ({len(migrations)}):[/]")
        for svc in migrations:
            current = get_service_host(cfg, svc)
            target = cfg.get_hosts(svc)[0]
            console.print(f"  [cyan]{svc}[/]: [magenta]{current}[/] → [magenta]{target}[/]")
    if has_missing:
        console.print(f"[green]Services to start ({len(missing)}):[/]")
        for svc in missing:
            console.print(f"  [cyan]{svc}[/] on [magenta]{format_host(cfg.get_hosts(svc))}[/]")
    if has_refresh:
        console.print(f"[blue]Services to refresh ({len(to_refresh)}):[/]")
        for svc in to_refresh:
            console.print(f"  [cyan]{svc}[/] on [magenta]{format_host(cfg.get_hosts(svc))}[/]")

    if dry_run:
        console.print(f"\n{MSG_DRY_RUN}")
        return

    # Execute changes
    console.print()
    all_results = []

    # 1. Stop orphaned services first
    if has_orphans:
        console.print("[yellow]Stopping orphaned services...[/]")
        all_results.extend(run_async(stop_orphaned_services(cfg)))

    # 2. Migrate services on wrong host
    if has_migrations:
        console.print("[cyan]Migrating services...[/]")
        migrate_results = run_async(up_services(cfg, migrations, raw=True))
        all_results.extend(migrate_results)
        maybe_regenerate_traefik(cfg, migrate_results)

    # 3. Start missing services (reuse up_services which handles state updates)
    if has_missing:
        console.print("[green]Starting missing services...[/]")
        start_results = run_async(up_services(cfg, missing, raw=True))
        all_results.extend(start_results)
        maybe_regenerate_traefik(cfg, start_results)

    # 4. Refresh remaining services (--full: run up to apply config changes)
    if has_refresh:
        console.print("[blue]Refreshing services...[/]")
        refresh_results = run_async(up_services(cfg, to_refresh, raw=True))
        all_results.extend(refresh_results)
        maybe_regenerate_traefik(cfg, refresh_results)

    report_results(all_results)


# Alias: cf a = cf apply
app.command("a", hidden=True)(apply)
