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

from compose_farm.console import (
    MSG_HOST_NOT_FOUND,
    MSG_STACK_NOT_FOUND,
    console,
    print_error,
    print_hint,
    print_success,
    print_warning,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator

    from compose_farm.config import Config
    from compose_farm.executor import CommandResult

_T = TypeVar("_T")
_R = TypeVar("_R")


# --- Shared CLI Options ---
StacksArg = Annotated[
    list[str] | None,
    typer.Argument(help="Stacks to operate on"),
]
AllOption = Annotated[
    bool,
    typer.Option("--all", "-a", help="Run on all stacks"),
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
    typer.Option("--host", "-H", help="Filter to stacks on this host"),
]
ServiceOption = Annotated[
    str | None,
    typer.Option("--service", "-s", help="Target a specific service within the stack"),
]

# --- Constants (internal) ---
_MISSING_PATH_PREVIEW_LIMIT = 2
_STATS_PREVIEW_LIMIT = 3  # Max number of pending migrations to show by name


def format_host(host: str | list[str]) -> str:
    """Format a host value for display."""
    if isinstance(host, list):
        return ", ".join(host)
    return host


@contextlib.contextmanager
def progress_bar(
    label: str, total: int, *, initial_description: str = "[dim]connecting...[/]"
) -> Generator[tuple[Progress, TaskID], None, None]:
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
        task_id = progress.add_task(initial_description, total=total)
        yield progress, task_id


def run_parallel_with_progress(
    label: str,
    items: list[_T],
    async_fn: Callable[[_T], Coroutine[None, None, _R]],
) -> list[_R]:
    """Run async tasks in parallel with a progress bar.

    Args:
        label: Progress bar label (e.g., "Discovering", "Querying hosts")
        items: List of items to process
        async_fn: Async function to call for each item, returns tuple where
                  first element is used for progress description

    Returns:
        List of results from async_fn in completion order.

    """

    async def gather() -> list[_R]:
        with progress_bar(label, len(items)) as (progress, task_id):
            tasks = [asyncio.create_task(async_fn(item)) for item in items]
            results: list[_R] = []
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results.append(result)
                progress.update(task_id, advance=1, description=f"[cyan]{result[0]}[/]")  # type: ignore[index]
            return results

    return asyncio.run(gather())


def load_config_or_exit(config_path: Path | None) -> Config:
    """Load config or exit with a friendly error message."""
    # Lazy import: pydantic adds ~50ms to startup, only load when actually needed
    from compose_farm.config import load_config  # noqa: PLC0415

    try:
        return load_config(config_path)
    except FileNotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1) from e
    except Exception as e:
        print_error(f"Invalid config: {e}")
        raise typer.Exit(1) from e


def get_stacks(
    stacks: list[str],
    all_stacks: bool,
    config_path: Path | None,
    *,
    host: str | None = None,
    default_all: bool = False,
) -> tuple[list[str], Config]:
    """Resolve stack list and load config.

    Handles three mutually exclusive selection methods:
    - Explicit stack names
    - --all flag
    - --host filter

    Args:
        stacks: Explicit stack names
        all_stacks: Whether --all was specified
        config_path: Path to config file
        host: Filter to stacks on this host
        default_all: If True, default to all stacks when nothing specified (for ps)

    Supports "." as shorthand for the current directory name.

    """
    validate_stack_selection(stacks, all_stacks, host)
    config = load_config_or_exit(config_path)

    if host is not None:
        validate_hosts(config, host)
        stack_list = [s for s in config.stacks if host in config.get_hosts(s)]
        if not stack_list:
            print_warning(f"No stacks configured for host [magenta]{host}[/]")
            raise typer.Exit(0)
        return stack_list, config

    if all_stacks:
        return list(config.stacks.keys()), config

    if not stacks:
        if default_all:
            return list(config.stacks.keys()), config
        print_error("Specify stacks or use [bold]--all[/] / [bold]--host[/]")
        raise typer.Exit(1)

    # Resolve "." to current directory name
    resolved = [Path.cwd().name if stack == "." else stack for stack in stacks]

    # Validate all stacks exist in config
    validate_stacks(
        config, resolved, hint="Add the stack to compose-farm.yaml or use [bold]--all[/]"
    )

    return resolved, config


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
                print_error(f"[cyan]{r.stack}[/] failed with exit code {r.exit_code}")
            console.print()
            console.print(
                f"[green]✓[/] {len(succeeded)}/{len(results)} stacks succeeded, "
                f"[red]✗[/] {len(failed)} failed"
            )
        else:
            print_success(f"All {len(results)} stacks succeeded")

    elif failed:
        # Single stack failed
        r = failed[0]
        print_error(f"[cyan]{r.stack}[/] failed with exit code {r.exit_code}")

    if failed:
        raise typer.Exit(1)


def maybe_regenerate_traefik(
    cfg: Config,
    results: list[CommandResult] | None = None,
) -> None:
    """Regenerate traefik config if traefik_file is configured.

    If results are provided, skips regeneration if all stacks failed.
    """
    if cfg.traefik_file is None:
        return

    # Skip if all stacks failed
    if results and not any(r.success for r in results):
        return

    # Lazy import: traefik/yaml adds startup time, only load when traefik_file is configured
    from compose_farm.traefik import (  # noqa: PLC0415
        generate_traefik_config,
        render_traefik_config,
    )

    try:
        dynamic, warnings = generate_traefik_config(cfg, list(cfg.stacks.keys()))
        new_content = render_traefik_config(dynamic)

        # Check if content changed
        old_content = ""
        if cfg.traefik_file.exists():
            old_content = cfg.traefik_file.read_text()

        if new_content != old_content:
            cfg.traefik_file.parent.mkdir(parents=True, exist_ok=True)
            cfg.traefik_file.write_text(new_content)
            console.print()  # Ensure we're on a new line after streaming output
            print_success(f"Traefik config updated: {cfg.traefik_file}")

        for warning in warnings:
            print_warning(warning)
    except (FileNotFoundError, ValueError) as exc:
        print_warning(f"Failed to update traefik config: {exc}")


def validate_stacks(cfg: Config, stacks: list[str], *, hint: str | None = None) -> None:
    """Validate that all stacks exist in config. Exits with error if any not found."""
    invalid = [s for s in stacks if s not in cfg.stacks]
    if invalid:
        for svc in invalid:
            print_error(MSG_STACK_NOT_FOUND.format(name=svc))
        if hint:
            print_hint(hint)
        raise typer.Exit(1)


def validate_hosts(cfg: Config, hosts: str | list[str]) -> None:
    """Validate that host(s) exist in config. Exits with error if any not found."""
    host_list = [hosts] if isinstance(hosts, str) else hosts
    invalid = [h for h in host_list if h not in cfg.hosts]
    if invalid:
        for h in invalid:
            print_error(MSG_HOST_NOT_FOUND.format(name=h))
        raise typer.Exit(1)


def validate_host_for_stack(cfg: Config, stack: str, host: str) -> None:
    """Validate that a host is valid for a stack."""
    validate_hosts(cfg, host)
    allowed_hosts = cfg.get_hosts(stack)
    if host not in allowed_hosts:
        print_error(
            f"Stack [cyan]{stack}[/] is not configured for host [magenta]{host}[/] "
            f"(configured: {', '.join(allowed_hosts)})"
        )
        raise typer.Exit(1)


def validate_stack_selection(
    stacks: list[str] | None,
    all_stacks: bool,
    host: str | None,
) -> None:
    """Validate that only one stack selection method is used.

    The three selection methods (explicit stacks, --all, --host) are mutually
    exclusive. This ensures consistent behavior across all commands.
    """
    methods = sum([bool(stacks), all_stacks, host is not None])
    if methods > 1:
        print_error("Use only one of: stack names, [bold]--all[/], or [bold]--host[/]")
        raise typer.Exit(1)
