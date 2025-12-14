"""CLI interface using Typer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypeVar

import typer
import yaml
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

from . import __version__
from .config import Config, load_config
from .executor import CommandResult, run_command, run_on_services, run_sequential_on_services
from .logs import snapshot_services
from .operations import (
    check_host_compatibility,
    check_mounts_on_configured_hosts,
    check_networks_on_configured_hosts,
    discover_running_services,
    up_services,
)
from .state import get_services_needing_migration, load_state, remove_service, save_state
from .traefik import generate_traefik_config

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

T = TypeVar("T")

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


def _load_config_or_exit(config_path: Path | None) -> Config:
    """Load config or exit with a friendly error message."""
    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        err_console.print(f"[red]✗[/] {e}")
        raise typer.Exit(1) from e


def _maybe_regenerate_traefik(cfg: Config) -> None:
    """Regenerate traefik config if traefik_file is configured."""
    if cfg.traefik_file is None:
        return

    try:
        dynamic, warnings = generate_traefik_config(cfg, list(cfg.services.keys()))
        new_content = yaml.safe_dump(dynamic, sort_keys=False)

        # Check if content changed
        old_content = ""
        if cfg.traefik_file.exists():
            old_content = cfg.traefik_file.read_text()

        if new_content != old_content:
            cfg.traefik_file.parent.mkdir(parents=True, exist_ok=True)
            cfg.traefik_file.write_text(new_content)
            console.print()  # Ensure we're on a new line after streaming output
            console.print(f"[green]✓[/] Traefik config updated: {cfg.traefik_file}")

        for warning in warnings:
            err_console.print(f"[yellow]![/] {warning}")
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"[yellow]![/] Failed to update traefik config: {exc}")


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"compose-farm {__version__}")
        raise typer.Exit


app = typer.Typer(
    name="compose-farm",
    help="Compose Farm - run docker compose commands across multiple hosts",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-v",
            help="Show version and exit",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Compose Farm - run docker compose commands across multiple hosts."""


def _get_services(
    services: list[str],
    all_services: bool,
    config_path: Path | None,
) -> tuple[list[str], Config]:
    """Resolve service list and load config."""
    config = _load_config_or_exit(config_path)

    if all_services:
        return list(config.services.keys()), config
    if not services:
        err_console.print("[red]✗[/] Specify services or use --all")
        raise typer.Exit(1)
    return list(services), config


def _run_async(coro: Coroutine[None, None, T]) -> T:
    """Run async coroutine."""
    return asyncio.run(coro)


def _report_results(results: list[CommandResult]) -> None:
    """Report command results and exit with appropriate code."""
    failed = [r for r in results if not r.success]
    if failed:
        for r in failed:
            err_console.print(
                f"[cyan]\\[{r.service}][/] [red]Failed with exit code {r.exit_code}[/]"
            )
        raise typer.Exit(1)


ServicesArg = Annotated[
    list[str] | None,
    typer.Argument(help="Services to operate on"),
]
AllOption = Annotated[
    bool,
    typer.Option("--all", "-a", help="Run on all services"),
]
ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to config file"),
]
LogPathOption = Annotated[
    Path | None,
    typer.Option("--log-path", "-l", help="Path to Dockerfarm TOML log"),
]

MISSING_PATH_PREVIEW_LIMIT = 2


@app.command(rich_help_panel="Lifecycle")
def up(
    services: ServicesArg = None,
    all_services: AllOption = False,
    migrate: Annotated[
        bool, typer.Option("--migrate", "-m", help="Only services needing migration")
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Start services (docker compose up -d). Auto-migrates if host changed."""
    if migrate:
        cfg = _load_config_or_exit(config)
        svc_list = get_services_needing_migration(cfg)
        if not svc_list:
            console.print("[green]✓[/] No services need migration")
            return
        console.print(f"[cyan]Migrating {len(svc_list)} service(s):[/] {', '.join(svc_list)}")
    else:
        svc_list, cfg = _get_services(services or [], all_services, config)
    # Always use raw output - migrations are sequential anyway
    results = _run_async(up_services(cfg, svc_list, raw=True))
    _maybe_regenerate_traefik(cfg)
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def down(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Stop services (docker compose down)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = _run_async(run_on_services(cfg, svc_list, "down", raw=raw))

    # Remove from state on success
    for result in results:
        if result.success:
            remove_service(cfg, result.service)

    _maybe_regenerate_traefik(cfg)
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def pull(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Pull latest images (docker compose pull)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = _run_async(run_on_services(cfg, svc_list, "pull", raw=raw))
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def restart(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Restart services (down + up)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = _run_async(run_sequential_on_services(cfg, svc_list, ["down", "up -d"], raw=raw))
    _maybe_regenerate_traefik(cfg)
    _report_results(results)


@app.command(rich_help_panel="Lifecycle")
def update(
    services: ServicesArg = None,
    all_services: AllOption = False,
    config: ConfigOption = None,
) -> None:
    """Update services (pull + down + up)."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    raw = len(svc_list) == 1
    results = _run_async(
        run_sequential_on_services(cfg, svc_list, ["pull", "down", "up -d"], raw=raw)
    )
    _maybe_regenerate_traefik(cfg)
    _report_results(results)


@app.command(rich_help_panel="Monitoring")
def logs(
    services: ServicesArg = None,
    all_services: AllOption = False,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow logs")] = False,
    tail: Annotated[int, typer.Option("--tail", "-n", help="Number of lines")] = 100,
    config: ConfigOption = None,
) -> None:
    """Show service logs."""
    svc_list, cfg = _get_services(services or [], all_services, config)
    cmd = f"logs --tail {tail}"
    if follow:
        cmd += " -f"
    results = _run_async(run_on_services(cfg, svc_list, cmd))
    _report_results(results)


@app.command(rich_help_panel="Monitoring")
def ps(
    config: ConfigOption = None,
) -> None:
    """Show status of all services."""
    cfg = _load_config_or_exit(config)
    results = _run_async(run_on_services(cfg, list(cfg.services.keys()), "ps"))
    _report_results(results)


_STATS_PREVIEW_LIMIT = 3  # Max number of pending migrations to show by name


def _group_services_by_host(
    services: dict[str, str],
    hosts: Mapping[str, object],
) -> dict[str, list[str]]:
    """Group services by their assigned host."""
    by_host: dict[str, list[str]] = {h: [] for h in hosts}
    for service, host_name in services.items():
        if host_name in by_host:
            by_host[host_name].append(service)
    return by_host


def _get_container_counts_with_progress(cfg: Config) -> dict[str, int]:
    """Get container counts from all hosts with a progress bar."""
    import contextlib

    async def get_count(host_name: str) -> tuple[str, int]:
        host = cfg.hosts[host_name]
        result = await run_command(host, "docker ps -q | wc -l", host_name, stream=False)
        count = 0
        if result.success:
            with contextlib.suppress(ValueError):
                count = int(result.stdout.strip())
        return host_name, count

    async def gather_with_progress(progress: Progress, task_id: TaskID) -> dict[str, int]:
        hosts = list(cfg.hosts.keys())
        tasks = [asyncio.create_task(get_count(h)) for h in hosts]
        results: dict[str, int] = {}
        for coro in asyncio.as_completed(tasks):
            host_name, count = await coro
            results[host_name] = count
            progress.update(task_id, advance=1, description=f"[cyan]{host_name}[/]")
        return results

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,  # Clear progress bar when done
    ) as progress:
        task_id = progress.add_task("Querying hosts...", total=len(cfg.hosts))
        return asyncio.run(gather_with_progress(progress, task_id))


def _build_host_table(
    cfg: Config,
    services_by_host: dict[str, list[str]],
    running_by_host: dict[str, list[str]],
    container_counts: dict[str, int],
    *,
    show_containers: bool,
) -> Table:
    """Build the hosts table."""
    table = Table(title="Hosts", show_header=True, header_style="bold cyan")
    table.add_column("Host", style="magenta")
    table.add_column("Address")
    table.add_column("Configured", justify="right")
    table.add_column("Running", justify="right")
    if show_containers:
        table.add_column("Containers", justify="right")

    for host_name in sorted(cfg.hosts.keys()):
        host = cfg.hosts[host_name]
        configured = len(services_by_host[host_name])
        running = len(running_by_host[host_name])

        row = [
            host_name,
            host.address,
            str(configured),
            str(running) if running > 0 else "[dim]0[/]",
        ]
        if show_containers:
            count = container_counts.get(host_name, 0)
            row.append(str(count) if count > 0 else "[dim]0[/]")

        table.add_row(*row)
    return table


def _build_summary_table(cfg: Config, state: dict[str, str], pending: list[str]) -> Table:
    """Build the summary table."""
    on_disk = cfg.discover_compose_dirs()

    table = Table(title="Summary", show_header=False)
    table.add_column("Label", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("Total hosts", str(len(cfg.hosts)))
    table.add_row("Services (configured)", str(len(cfg.services)))
    table.add_row("Services (tracked)", str(len(state)))
    table.add_row("Compose files on disk", str(len(on_disk)))

    if pending:
        preview = ", ".join(pending[:_STATS_PREVIEW_LIMIT])
        suffix = "..." if len(pending) > _STATS_PREVIEW_LIMIT else ""
        table.add_row("Pending migrations", f"[yellow]{len(pending)}[/] ({preview}{suffix})")
    else:
        table.add_row("Pending migrations", "[green]0[/]")

    return table


@app.command(rich_help_panel="Monitoring")
def stats(
    live: Annotated[
        bool,
        typer.Option("--live", "-l", help="Query Docker for live container stats"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Show overview statistics for hosts and services.

    Without --live: Shows config/state info (hosts, services, pending migrations).
    With --live: Also queries Docker on each host for container counts.
    """
    cfg = _load_config_or_exit(config)
    state = load_state(cfg)
    pending = get_services_needing_migration(cfg)

    services_by_host = _group_services_by_host(cfg.services, cfg.hosts)
    running_by_host = _group_services_by_host(state, cfg.hosts)

    container_counts: dict[str, int] = {}
    if live:
        container_counts = _get_container_counts_with_progress(cfg)

    host_table = _build_host_table(
        cfg, services_by_host, running_by_host, container_counts, show_containers=live
    )
    console.print(host_table)

    console.print()
    console.print(_build_summary_table(cfg, state, pending))


@app.command("traefik-file", rich_help_panel="Configuration")
def traefik_file(
    services: ServicesArg = None,
    all_services: AllOption = False,
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
    svc_list, cfg = _get_services(services or [], all_services, config)
    try:
        dynamic, warnings = generate_traefik_config(cfg, svc_list)
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from exc

    rendered = yaml.safe_dump(dynamic, sort_keys=False)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        console.print(f"[green]✓[/] Traefik config written to {output}")
    else:
        console.print(rendered)

    for warning in warnings:
        err_console.print(f"[yellow]![/] {warning}")


def _report_sync_changes(
    added: list[str],
    removed: list[str],
    changed: list[tuple[str, str, str]],
    discovered: dict[str, str],
    current_state: dict[str, str],
) -> None:
    """Report sync changes to the user."""
    if added:
        console.print(f"\nNew services found ({len(added)}):")
        for service in sorted(added):
            console.print(f"  [green]+[/] [cyan]{service}[/] on [magenta]{discovered[service]}[/]")

    if changed:
        console.print(f"\nServices on different hosts ({len(changed)}):")
        for service, old_host, new_host in sorted(changed):
            console.print(
                f"  [yellow]~[/] [cyan]{service}[/]: "
                f"[magenta]{old_host}[/] → [magenta]{new_host}[/]"
            )

    if removed:
        console.print(f"\nServices no longer running ({len(removed)}):")
        for service in sorted(removed):
            console.print(
                f"  [red]-[/] [cyan]{service}[/] (was on [magenta]{current_state[service]}[/])"
            )


@app.command(rich_help_panel="Configuration")
def sync(
    config: ConfigOption = None,
    log_path: LogPathOption = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would be synced without writing"),
    ] = False,
) -> None:
    """Sync local state with running services.

    Discovers which services are running on which hosts, updates the state
    file, and captures image digests. Combines service discovery with
    image snapshot into a single command.
    """
    cfg = _load_config_or_exit(config)
    current_state = load_state(cfg)

    console.print("Discovering running services...")
    discovered = _run_async(discover_running_services(cfg))

    # Calculate changes
    added = [s for s in discovered if s not in current_state]
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
        console.print("[green]✓[/] State is already in sync.")

    if dry_run:
        console.print("\n[dim](dry-run: no changes made)[/]")
        return

    # Update state file
    if state_changed:
        save_state(cfg, discovered)
        console.print(f"\n[green]✓[/] State updated: {len(discovered)} services tracked.")

    # Capture image digests for running services
    if discovered:
        console.print("\nCapturing image digests...")
        try:
            path = _run_async(snapshot_services(cfg, list(discovered.keys()), log_path=log_path))
            console.print(f"[green]✓[/] Digests written to {path}")
        except RuntimeError as exc:
            err_console.print(f"[yellow]![/] {exc}")


def _report_config_status(cfg: Config) -> bool:
    """Check and report config vs disk status. Returns True if errors found."""
    configured = set(cfg.services.keys())
    on_disk = cfg.discover_compose_dirs()
    missing_from_config = sorted(on_disk - configured)
    missing_from_disk = sorted(configured - on_disk)

    if missing_from_config:
        console.print(f"\n[yellow]On disk but not in config[/] ({len(missing_from_config)}):")
        for name in missing_from_config:
            console.print(f"  [yellow]+[/] [cyan]{name}[/]")

    if missing_from_disk:
        console.print(f"\n[red]In config but no compose file[/] ({len(missing_from_disk)}):")
        for name in missing_from_disk:
            console.print(f"  [red]-[/] [cyan]{name}[/]")

    if not missing_from_config and not missing_from_disk:
        console.print("[green]✓[/] Config matches disk")

    return bool(missing_from_disk)


def _report_traefik_status(cfg: Config, services: list[str]) -> None:
    """Check and report traefik label status."""
    try:
        _, warnings = generate_traefik_config(cfg, services, check_all=True)
    except (FileNotFoundError, ValueError):
        return

    if warnings:
        console.print(f"\n[yellow]Traefik issues[/] ({len(warnings)}):")
        for warning in warnings:
            console.print(f"  [yellow]![/] {warning}")
    else:
        console.print("[green]✓[/] Traefik labels valid")


def _report_mount_errors(mount_errors: list[tuple[str, str, str]]) -> None:
    """Report mount errors grouped by service."""
    by_service: dict[str, list[tuple[str, str]]] = {}
    for svc, host, path in mount_errors:
        by_service.setdefault(svc, []).append((host, path))

    console.print(f"\n[red]Missing mounts[/] ({len(mount_errors)}):")
    for svc, items in sorted(by_service.items()):
        host = items[0][0]
        paths = [p for _, p in items]
        console.print(f"  [cyan]{svc}[/] on [magenta]{host}[/]:")
        for path in paths:
            console.print(f"    [red]✗[/] {path}")


def _report_network_errors(network_errors: list[tuple[str, str, str]]) -> None:
    """Report network errors grouped by service."""
    by_service: dict[str, list[tuple[str, str]]] = {}
    for svc, host, net in network_errors:
        by_service.setdefault(svc, []).append((host, net))

    console.print(f"\n[red]Missing networks[/] ({len(network_errors)}):")
    for svc, items in sorted(by_service.items()):
        host = items[0][0]
        networks = [n for _, n in items]
        console.print(f"  [cyan]{svc}[/] on [magenta]{host}[/]:")
        for net in networks:
            console.print(f"    [red]✗[/] {net}")


def _report_host_compatibility(
    compat: dict[str, tuple[int, int, list[str]]],
    current_host: str,
) -> None:
    """Report host compatibility for a service."""
    for host_name, (found, total, missing) in sorted(compat.items()):
        is_current = host_name == current_host
        marker = " [dim](assigned)[/]" if is_current else ""

        if found == total:
            console.print(f"  [green]✓[/] [magenta]{host_name}[/] {found}/{total}{marker}")
        else:
            preview = ", ".join(missing[:MISSING_PATH_PREVIEW_LIMIT])
            if len(missing) > MISSING_PATH_PREVIEW_LIMIT:
                preview += f", +{len(missing) - MISSING_PATH_PREVIEW_LIMIT} more"
            console.print(
                f"  [red]✗[/] [magenta]{host_name}[/] {found}/{total} "
                f"[dim](missing: {preview})[/]{marker}"
            )


@app.command(rich_help_panel="Configuration")
def check(
    services: ServicesArg = None,
    local: Annotated[
        bool,
        typer.Option("--local", help="Skip SSH-based checks (faster)"),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Validate configuration, traefik labels, mounts, and networks.

    Without arguments: validates all services against configured hosts.
    With service arguments: validates specific services and shows host compatibility.

    Use --local to skip SSH-based checks for faster validation.
    """
    cfg = _load_config_or_exit(config)

    # Determine which services to check and whether to show host compatibility
    if services:
        svc_list = list(services)
        invalid = [s for s in svc_list if s not in cfg.services]
        if invalid:
            for svc in invalid:
                err_console.print(f"[red]✗[/] Service '{svc}' not found in config")
            raise typer.Exit(1)
        show_host_compat = True
    else:
        svc_list = list(cfg.services.keys())
        show_host_compat = False

    # Run checks
    has_errors = _report_config_status(cfg)
    _report_traefik_status(cfg, svc_list)

    if not local:
        console.print("\nChecking mounts and networks...")
        mount_errors = _run_async(check_mounts_on_configured_hosts(cfg, svc_list))
        network_errors = _run_async(check_networks_on_configured_hosts(cfg, svc_list))

        if mount_errors:
            _report_mount_errors(mount_errors)
            has_errors = True
        if network_errors:
            _report_network_errors(network_errors)
            has_errors = True
        if not mount_errors and not network_errors:
            console.print("[green]✓[/] All mounts and networks exist")

        if show_host_compat:
            for service in svc_list:
                console.print(f"\n[bold]Host compatibility for[/] [cyan]{service}[/]:")
                compat = _run_async(check_host_compatibility(cfg, service))
                _report_host_compatibility(compat, cfg.services[service])

    if has_errors:
        raise typer.Exit(1)


# Default network settings for cross-host Docker networking
DEFAULT_NETWORK_NAME = "mynetwork"
DEFAULT_NETWORK_SUBNET = "172.20.0.0/16"
DEFAULT_NETWORK_GATEWAY = "172.20.0.1"


@app.command("init-network", rich_help_panel="Configuration")
def init_network(
    hosts: Annotated[
        list[str] | None,
        typer.Argument(help="Hosts to create network on (default: all)"),
    ] = None,
    network: Annotated[
        str,
        typer.Option("--network", "-n", help="Network name"),
    ] = DEFAULT_NETWORK_NAME,
    subnet: Annotated[
        str,
        typer.Option("--subnet", "-s", help="Network subnet"),
    ] = DEFAULT_NETWORK_SUBNET,
    gateway: Annotated[
        str,
        typer.Option("--gateway", "-g", help="Network gateway"),
    ] = DEFAULT_NETWORK_GATEWAY,
    config: ConfigOption = None,
) -> None:
    """Create Docker network on hosts with consistent settings.

    Creates an external Docker network that services can use for cross-host
    communication. Uses the same subnet/gateway on all hosts to ensure
    consistent networking.
    """
    cfg = _load_config_or_exit(config)

    target_hosts = list(hosts) if hosts else list(cfg.hosts.keys())
    invalid = [h for h in target_hosts if h not in cfg.hosts]
    if invalid:
        for h in invalid:
            err_console.print(f"[red]✗[/] Host '{h}' not found in config")
        raise typer.Exit(1)

    async def create_network_on_host(host_name: str) -> CommandResult:
        host = cfg.hosts[host_name]
        # Check if network already exists
        check_cmd = f"docker network inspect '{network}' >/dev/null 2>&1"
        check_result = await run_command(host, check_cmd, host_name, stream=False)

        if check_result.success:
            console.print(f"[cyan]\\[{host_name}][/] Network '{network}' already exists")
            return CommandResult(service=host_name, exit_code=0, success=True)

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
            err_console.print(
                f"[cyan]\\[{host_name}][/] [red]✗[/] Failed to create network: "
                f"{result.stderr.strip()}"
            )

        return result

    async def run_all() -> list[CommandResult]:
        return await asyncio.gather(*[create_network_on_host(h) for h in target_hosts])

    results = _run_async(run_all())
    failed = [r for r in results if not r.success]
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
