"""Tests for console helpers."""

from compose_farm.console import _LazyConsole


def test_lazy_console_supports_context_manager() -> None:
    """Rich Progress enters the provided console as a context manager."""
    console = _LazyConsole()

    with console as rich_console:
        assert rich_console is not None
