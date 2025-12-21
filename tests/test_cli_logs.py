"""Tests for CLI logs command."""

from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import typer

from compose_farm.cli.monitoring import logs
from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult


def _make_config(tmp_path: Path) -> Config:
    """Create a minimal config for testing."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    for svc in ("svc1", "svc2", "svc3"):
        svc_dir = compose_dir / svc
        svc_dir.mkdir()
        (svc_dir / "docker-compose.yml").write_text("services: {}\n")

    return Config(
        compose_dir=compose_dir,
        hosts={"local": Host(address="localhost"), "remote": Host(address="192.168.1.10")},
        stacks={"svc1": "local", "svc2": "local", "svc3": "remote"},
    )


def _make_result(stack: str) -> CommandResult:
    """Create a successful command result."""
    return CommandResult(stack=stack, exit_code=0, success=True, stdout="", stderr="")


def _mock_run_async_factory(
    stacks: list[str],
) -> tuple[Any, list[CommandResult]]:
    """Create a mock run_async that returns results for given stacks."""
    results = [_make_result(s) for s in stacks]

    def mock_run_async(_coro: Coroutine[Any, Any, Any]) -> list[CommandResult]:
        return results

    return mock_run_async, results


class TestLogsContextualDefault:
    """Tests for logs --tail contextual default behavior."""

    def test_logs_all_stacks_defaults_to_20(self, tmp_path: Path) -> None:
        """When --all is specified, default tail should be 20."""
        cfg = _make_config(tmp_path)
        mock_run_async, _ = _mock_run_async_factory(["svc1", "svc2", "svc3"])

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.common.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.monitoring.run_async", side_effect=mock_run_async),
            patch("compose_farm.cli.monitoring.run_on_stacks") as mock_run,
        ):
            mock_run.return_value = None

            logs(stacks=None, all_stacks=True, host=None, follow=False, tail=None, config=None)

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][2] == "logs --tail 20"

    def test_logs_single_stack_defaults_to_100(self, tmp_path: Path) -> None:
        """When specific stacks are specified, default tail should be 100."""
        cfg = _make_config(tmp_path)
        mock_run_async, _ = _mock_run_async_factory(["svc1"])

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.common.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.monitoring.run_async", side_effect=mock_run_async),
            patch("compose_farm.cli.monitoring.run_on_stacks") as mock_run,
        ):
            logs(
                stacks=["svc1"],
                all_stacks=False,
                host=None,
                follow=False,
                tail=None,
                config=None,
            )

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][2] == "logs --tail 100"

    def test_logs_explicit_tail_overrides_default(self, tmp_path: Path) -> None:
        """When --tail is explicitly provided, it should override the default."""
        cfg = _make_config(tmp_path)
        mock_run_async, _ = _mock_run_async_factory(["svc1", "svc2", "svc3"])

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.common.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.monitoring.run_async", side_effect=mock_run_async),
            patch("compose_farm.cli.monitoring.run_on_stacks") as mock_run,
        ):
            logs(
                stacks=None,
                all_stacks=True,
                host=None,
                follow=False,
                tail=50,
                config=None,
            )

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][2] == "logs --tail 50"

    def test_logs_follow_appends_flag(self, tmp_path: Path) -> None:
        """When --follow is specified, -f should be appended to command."""
        cfg = _make_config(tmp_path)
        mock_run_async, _ = _mock_run_async_factory(["svc1"])

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.common.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.monitoring.run_async", side_effect=mock_run_async),
            patch("compose_farm.cli.monitoring.run_on_stacks") as mock_run,
        ):
            logs(
                stacks=["svc1"],
                all_stacks=False,
                host=None,
                follow=True,
                tail=None,
                config=None,
            )

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][2] == "logs --tail 100 -f"


class TestLogsHostFilter:
    """Tests for logs --host filter behavior."""

    def test_logs_host_filter_selects_stacks_on_host(self, tmp_path: Path) -> None:
        """When --host is specified, only stacks on that host are included."""
        cfg = _make_config(tmp_path)
        mock_run_async, _ = _mock_run_async_factory(["svc1", "svc2"])

        with (
            patch("compose_farm.cli.common.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.monitoring.run_async", side_effect=mock_run_async),
            patch("compose_farm.cli.monitoring.run_on_stacks") as mock_run,
        ):
            logs(
                stacks=None,
                all_stacks=False,
                host="local",
                follow=False,
                tail=None,
                config=None,
            )

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            # svc1 and svc2 are on "local", svc3 is on "remote"
            assert set(call_args[0][1]) == {"svc1", "svc2"}

    def test_logs_host_filter_defaults_to_20_lines(self, tmp_path: Path) -> None:
        """When --host is specified, default tail should be 20 (multiple stacks)."""
        cfg = _make_config(tmp_path)
        mock_run_async, _ = _mock_run_async_factory(["svc1", "svc2"])

        with (
            patch("compose_farm.cli.common.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.monitoring.run_async", side_effect=mock_run_async),
            patch("compose_farm.cli.monitoring.run_on_stacks") as mock_run,
        ):
            logs(
                stacks=None,
                all_stacks=False,
                host="local",
                follow=False,
                tail=None,
                config=None,
            )

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][2] == "logs --tail 20"

    def test_logs_all_and_host_mutually_exclusive(self) -> None:
        """Using --all and --host together should error."""
        # No config mock needed - error is raised before config is loaded
        with pytest.raises(typer.Exit) as exc_info:
            logs(
                stacks=None,
                all_stacks=True,
                host="local",
                follow=False,
                tail=None,
                config=None,
            )

        assert exc_info.value.exit_code == 1
