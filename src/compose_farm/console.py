"""Shared console instances for consistent output styling."""

from __future__ import annotations

from hashlib import blake2b
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

_STACK_PREFIX_STYLES = (
    "cyan",
    "green",
    "yellow",
    "blue",
    "magenta",
    "bright_cyan",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_magenta",
)


class _LazyConsole:
    """Create a Rich console only when command output actually needs one."""

    def __init__(self, *, stderr: bool = False) -> None:
        self._stderr = stderr
        self._console: Console | None = None

    def _get(self) -> Console:
        if self._console is None:
            # Lazy import: Rich console setup is not needed while building `cf --help`.
            from rich.console import Console  # noqa: PLC0415

            self._console = Console(stderr=self._stderr, highlight=False)
        return self._console

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Proxy print calls to the underlying Rich console."""
        self._get().print(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)


console: Any = _LazyConsole()
err_console: Any = _LazyConsole(stderr=True)


def _stack_prefix_style(stack: str) -> str:
    digest = blake2b(stack.encode("utf-8"), digest_size=1).digest()[0]
    return _STACK_PREFIX_STYLES[digest % len(_STACK_PREFIX_STYLES)]


def format_stack_prefix(stack: str) -> str:
    """Format a bracketed stack prefix with a stable per-stack color."""
    # Lazy import: Rich markup escaping is only needed when rendering stack prefixes.
    from rich.markup import escape  # noqa: PLC0415

    return f"[{_stack_prefix_style(stack)}]\\[{escape(stack)}][/]"


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
