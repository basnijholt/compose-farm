"""Tests for WebSocket handler module."""

from __future__ import annotations


class TestShellCommand:
    """Tests for shell command configuration."""

    def test_shell_command_does_not_suppress_stderr(self) -> None:
        """Ensure shell command doesn't redirect stderr to /dev/null.

        Regression test for bug where `2>/dev/null` caused all command
        errors (like 'command not found') to be silently swallowed.
        """
        import inspect

        from compose_farm.web.ws import _run_shell_session

        # Get the source code of the function
        source = inspect.getsource(_run_shell_session)

        # Ensure we're not suppressing stderr
        assert "2>/dev/null" not in source, (
            "Shell command must not redirect stderr to /dev/null - "
            "this swallows command errors like 'command not found'"
        )
