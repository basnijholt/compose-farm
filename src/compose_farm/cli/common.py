"""Shared CLI helpers, options, and utilities."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypeVar

import typer
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from compose_farm.console import console, err_console

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator

    from compose_farm.config import Config
    from compose_farm.executor import CommandResult

_T = TypeVar("_T")


# --- Shared CLI Options ---
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
HostOption = Annotated[
    str | None,
    typer.Option("--host", "-H", help="Filter to services on this host"),
]

# --- Constants (internal) ---
_MISSING_PATH_PREVIEW_LIMIT = 2
_STATS_PREVIEW_LIMIT = 3  # Max number of pending migrations to show by name


@contextlib.contextmanager
def progress_bar(label: str, total: int) -> Generator[tuple[Progress, TaskID], None, None]:
    """Create a standardized progress bar with consistent styling.

    Yields (progress, task_id). Use progress.update(task_id, advance=1, description=...)
    to advance.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn(f"[bold blue]{label}[/]"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("", total=total)
        yield progress, task_id


def load_config_or_exit(config_path: Path | None) -> Config:
    """Load config or exit with a friendly error message."""
    # Lazy import: pydantic adds ~50ms to startup, only load when actually needed
    from compose_farm.config import load_config  # noqa: PLC0415

    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        err_console.print(f"[red]✗[/] {e}")
        raise typer.Exit(1) from e


def get_services(
    services: list[str],
    all_services: bool,
    config_path: Path | None,
) -> tuple[list[str], Config]:
    """Resolve service list and load config."""
    config = load_config_or_exit(config_path)

    if all_services:
        return list(config.services.keys()), config
    if not services:
        err_console.print("[red]✗[/] Specify services or use --all")
        raise typer.Exit(1)
    return list(services), config


def run_async(coro: Coroutine[None, None, _T]) -> _T:
    """Run async coroutine."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/]")
        raise typer.Exit(130) from None  # Standard exit code for SIGINT


def report_results(results: list[CommandResult]) -> None:
    """Report command results and exit with appropriate code."""
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    # Always print summary when there are multiple results
    if len(results) > 1:
        console.print()  # Blank line before summary
        if failed:
            for r in failed:
                err_console.print(
                    f"[red]✗[/] [cyan]{r.service}[/] failed with exit code {r.exit_code}"
                )
            console.print()
            console.print(
                f"[green]✓[/] {len(succeeded)}/{len(results)} services succeeded, "
                f"[red]✗[/] {len(failed)} failed"
            )
        else:
            console.print(f"[green]✓[/] All {len(results)} services succeeded")

    elif failed:
        # Single service failed
        r = failed[0]
        err_console.print(f"[red]✗[/] [cyan]{r.service}[/] failed with exit code {r.exit_code}")

    if failed:
        raise typer.Exit(1)


def maybe_regenerate_traefik(
    cfg: Config,
    results: list[CommandResult] | None = None,
) -> None:
    """Regenerate traefik config if traefik_file is configured.

    If results are provided, skips regeneration if all services failed.
    """
    if cfg.traefik_file is None:
        return

    # Skip if all services failed
    if results and not any(r.success for r in results):
        return

    # Lazy import: traefik/yaml adds startup time, only load when traefik_file is configured
    from compose_farm.traefik import (  # noqa: PLC0415
        generate_traefik_config,
        render_traefik_config,
    )

    try:
        dynamic, warnings = generate_traefik_config(cfg, list(cfg.services.keys()))
        new_content = render_traefik_config(dynamic)

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


def validate_host_for_service(cfg: Config, service: str, host: str) -> None:
    """Validate that a host is valid for a service."""
    if host not in cfg.hosts:
        err_console.print(f"[red]✗[/] Host '{host}' not found in config")
        raise typer.Exit(1)
    allowed_hosts = cfg.get_hosts(service)
    if host not in allowed_hosts:
        err_console.print(
            f"[red]✗[/] Service '{service}' is not configured for host '{host}' "
            f"(configured: {', '.join(allowed_hosts)})"
        )
        raise typer.Exit(1)


def run_host_operation(
    cfg: Config,
    svc_list: list[str],
    host: str,
    command: str,
    action_verb: str,
    state_callback: Callable[[Config, str, str], None],
) -> None:
    """Run an operation on a specific host for multiple services."""
    from compose_farm.executor import run_compose_on_host  # noqa: PLC0415

    results: list[CommandResult] = []
    for service in svc_list:
        validate_host_for_service(cfg, service, host)
        console.print(f"[cyan]\\[{service}][/] {action_verb} on [magenta]{host}[/]...")
        result = run_async(run_compose_on_host(cfg, service, host, command, raw=True))
        print()  # Newline after raw output
        results.append(result)
        if result.success:
            state_callback(cfg, service, host)
    maybe_regenerate_traefik(cfg, results)
    report_results(results)
