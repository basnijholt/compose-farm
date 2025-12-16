"""Lifecycle commands: up, down, pull, restart, update."""

from __future__ import annotations

from typing import Annotated

import typer

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
from compose_farm.console import console
from compose_farm.executor import run_on_services, run_sequential_on_services
from compose_farm.operations import up_services
from compose_farm.state import (
    add_service_to_host,
    get_services_needing_migration,
    remove_service,
    remove_service_from_host,
)


def register_commands(app: typer.Typer) -> None:  # noqa: PLR0915
    """Register lifecycle commands on the app."""

    @app.command(rich_help_panel="Lifecycle")
    def up(
        services: ServicesArg = None,
        all_services: AllOption = False,
        migrate: Annotated[
            bool, typer.Option("--migrate", "-m", help="Only services needing migration")
        ] = False,
        host: HostOption = None,
        config: ConfigOption = None,
    ) -> None:
        """Start services (docker compose up -d). Auto-migrates if host changed."""
        from compose_farm.console import err_console  # noqa: PLC0415

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
        maybe_regenerate_traefik(cfg)
        report_results(results)

    @app.command(rich_help_panel="Lifecycle")
    def down(
        services: ServicesArg = None,
        all_services: AllOption = False,
        host: HostOption = None,
        config: ConfigOption = None,
    ) -> None:
        """Stop services (docker compose down)."""
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

        maybe_regenerate_traefik(cfg)
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
        maybe_regenerate_traefik(cfg)
        report_results(results)

    @app.command(rich_help_panel="Lifecycle")
    def update(
        services: ServicesArg = None,
        all_services: AllOption = False,
        config: ConfigOption = None,
    ) -> None:
        """Update services (pull + down + up)."""
        svc_list, cfg = get_services(services or [], all_services, config)
        raw = len(svc_list) == 1
        results = run_async(
            run_sequential_on_services(cfg, svc_list, ["pull", "down", "up -d"], raw=raw)
        )
        maybe_regenerate_traefik(cfg)
        report_results(results)
