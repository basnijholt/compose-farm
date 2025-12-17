"""Lifecycle commands: up, down, pull, restart, update, apply."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from compose_farm.config import Config

from compose_farm.cli.app import app
from compose_farm.cli.common import (
    AllOption,
    ConfigOption,
    HostOption,
    ServicesArg,
    get_services,
    load_config_or_exit,
    maybe_regenerate_traefik,
    report_results,
    run_async,
    run_host_operation,
)
from compose_farm.console import console, err_console
from compose_farm.executor import run_on_services, run_sequential_on_services
from compose_farm.operations import stop_orphaned_services, up_services
from compose_farm.state import (
    add_service_to_host,
    get_orphaned_services,
    get_service_host,
    get_services_needing_migration,
    remove_service,
    remove_service_from_host,
)


@app.command(rich_help_panel="Lifecycle")
def up(
    services: ServicesArg = None,
    all_services: AllOption = False,
    migrate: Annotated[
        bool,
        typer.Option(
            "--migrate", "-m", help="Find and migrate services where host differs from config"
        ),
    ] = False,
    host: HostOption = None,
    config: ConfigOption = None,
) -> None:
    """Start services (docker compose up -d). Auto-migrates if host changed."""
    if migrate and host:
        err_console.print("[red]✗[/] Cannot use --migrate and --host together")
        raise typer.Exit(1)

    if migrate:
        cfg = load_config_or_exit(config)
        svc_list = get_services_needing_migration(cfg)

        if not svc_list:
            console.print("[green]✓[/] No services need migration")
            return

        console.print(f"[cyan]Migrating {len(svc_list)} service(s):[/] {', '.join(svc_list)}")
    else:
        svc_list, cfg = get_services(services or [], all_services, config)

    # Per-host operation: run on specific host only
    if host:
        run_host_operation(cfg, svc_list, host, "up -d", "Starting", add_service_to_host)
        return

    # Normal operation: use up_services with migration logic
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
    # Handle --orphaned flag
    if orphaned:
        if services or all_services or host:
            err_console.print("[red]✗[/] Cannot use --orphaned with services, --all, or --host")
            raise typer.Exit(1)

        cfg = load_config_or_exit(config)
        orphaned_services = get_orphaned_services(cfg)

        if not orphaned_services:
            console.print("[green]✓[/] No orphaned services to stop")
            return

        console.print(
            f"[yellow]Stopping {len(orphaned_services)} orphaned service(s):[/] "
            f"{', '.join(orphaned_services.keys())}"
        )
        results = run_async(stop_orphaned_services(cfg))
        report_results(results)
        return

    svc_list, cfg = get_services(services or [], all_services, config)

    # Per-host operation: run on specific host only
    if host:
        run_host_operation(cfg, svc_list, host, "down", "Stopping", remove_service_from_host)
        return

    # Normal operation
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


def _format_host(host: str | list[str]) -> str:
    """Format a host value for display."""
    if isinstance(host, list):
        return ", ".join(host)
    return host


def _report_pending_migrations(cfg: Config, migrations: list[str]) -> None:
    """Report services that need migration."""
    console.print(f"[cyan]Services to migrate ({len(migrations)}):[/]")
    for svc in migrations:
        current = get_service_host(cfg, svc)
        target = cfg.get_hosts(svc)[0]
        console.print(f"  [cyan]{svc}[/]: [magenta]{current}[/] → [magenta]{target}[/]")


def _report_pending_orphans(orphaned: dict[str, str | list[str]]) -> None:
    """Report orphaned services that will be stopped."""
    console.print(f"[yellow]Orphaned services to stop ({len(orphaned)}):[/]")
    for svc, hosts in orphaned.items():
        console.print(f"  [cyan]{svc}[/] on [magenta]{_format_host(hosts)}[/]")


@app.command(rich_help_panel="Lifecycle")
def apply(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would change without executing"),
    ] = False,
    no_orphans: Annotated[
        bool,
        typer.Option("--no-orphans", help="Only migrate, don't stop orphaned services"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Make reality match config (migrate services + stop orphans).

    This is the "reconcile" command that ensures running services match your
    config file. It will:

    1. Migrate services that are on the wrong host (host in state ≠ host in config)
    2. Stop orphaned services (in state but removed from config)

    Use --dry-run to preview changes before applying.
    Use --no-orphans to only migrate without stopping orphaned services.
    """
    cfg = load_config_or_exit(config)
    migrations = get_services_needing_migration(cfg)
    orphaned = get_orphaned_services(cfg)

    has_migrations = bool(migrations)
    has_orphans = bool(orphaned) and not no_orphans

    if not has_migrations and not has_orphans:
        console.print("[green]✓[/] Nothing to apply - reality matches config")
        return

    # Report what will be done
    if has_migrations:
        _report_pending_migrations(cfg, migrations)
    if has_orphans:
        _report_pending_orphans(orphaned)

    if dry_run:
        console.print("\n[dim](dry-run: no changes made)[/]")
        return

    # Execute changes
    console.print()
    all_results = []

    if has_orphans:
        console.print("[yellow]Stopping orphaned services...[/]")
        all_results.extend(run_async(stop_orphaned_services(cfg)))

    if has_migrations:
        console.print("[cyan]Migrating services...[/]")
        migrate_results = run_async(up_services(cfg, migrations, raw=True))
        all_results.extend(migrate_results)
        maybe_regenerate_traefik(cfg, migrate_results)

    report_results(all_results)
