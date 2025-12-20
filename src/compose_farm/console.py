"""Shared console instances for consistent output styling."""

from rich.console import Console

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


# --- Message Constants ---
# Standardized message templates for consistent user-facing output

MSG_STACK_NOT_FOUND = "Stack [cyan]{name}[/] not found in config"
MSG_HOST_NOT_FOUND = "Host [magenta]{name}[/] not found in config"
MSG_CONFIG_NOT_FOUND = "Config file not found"
MSG_DRY_RUN = "[dim](dry-run: no changes made)[/]"


# --- Message Helper Functions ---


def print_error(msg: str) -> None:
    """Print error message with ✗ prefix to stderr."""
    err_console.print(f"[red]✗[/] {msg}")


def print_success(msg: str) -> None:
    """Print success message with ✓ prefix to stdout."""
    console.print(f"[green]✓[/] {msg}")


def print_warning(msg: str) -> None:
    """Print warning message with ! prefix to stderr."""
    err_console.print(f"[yellow]![/] {msg}")


def print_hint(msg: str) -> None:
    """Print hint message in dim style to stdout."""
    console.print(f"[dim]Hint: {msg}[/]")
