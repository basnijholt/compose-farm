"""Shared console instances for consistent output styling."""

from rich.console import Console

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)
