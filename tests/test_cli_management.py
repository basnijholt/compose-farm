"""Tests for CLI management helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer

from compose_farm.cli import management
from compose_farm.cli.management import plugins
from compose_farm.config import Config, Host
from compose_farm.operations import PreflightResult
from compose_farm.plugins import HookEvent, HookExecutionError, HookPolicy, HookRegistration
from compose_farm.plugins.manager import HookManager


def test_check_stack_requirements_aggregates_remote_check_errors(tmp_path: Path) -> None:
    """Cf check should keep remote check failures separate from missing resources."""
    config = Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="localhost")},
        stacks={"svc": "host1"},
    )
    preflight = PreflightResult(
        missing_paths=[],
        missing_networks=[],
        missing_devices=[],
        check_errors=["mount-check failed on host1: Permission denied"],
    )

    with patch(
        "compose_farm.cli.management.check_stack_requirements",
        new_callable=AsyncMock,
        return_value=preflight,
    ):
        mount_errors, network_errors, device_errors, preflight_errors = (
            management._check_stack_requirements(config, ["svc"])
        )

    assert mount_errors == []
    assert network_errors == []
    assert device_errors == []
    assert preflight_errors == [("svc", "host1", "mount-check failed on host1: Permission denied")]


def _make_config(tmp_path: Path, *, enabled_plugins: list[str] | None = None) -> Config:
    """Create a minimal config for management CLI tests."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text("")
    return Config(
        compose_dir=compose_dir,
        hosts={"host1": Host(address="localhost")},
        stacks={"svc": "host1"},
        plugins=enabled_plugins or [],
        config_path=config_path,
    )


class TestPluginsCommand:
    """Tests for cf plugins command."""

    def test_plugins_no_enabled_plugins(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Shows available plugins and notes when none are enabled."""
        cfg = _make_config(tmp_path)

        with (
            patch("compose_farm.cli.management.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.management.list_available_plugins", return_value=["lab"]),
        ):
            plugins(config=None)

        captured = capsys.readouterr()
        assert "Available plugins" in captured.out
        assert "lab" in captured.out
        assert "No plugins enabled in config" in captured.out

    def test_plugins_exits_when_configured_plugin_missing(self, tmp_path: Path) -> None:
        """Configured plugin not found raises CLI exit."""
        cfg = _make_config(tmp_path, enabled_plugins=["missing"])

        with (
            patch("compose_farm.cli.management.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.management.list_available_plugins", return_value=[]),
            patch(
                "compose_farm.cli.management.HookManager.from_config",
                side_effect=HookExecutionError("Configured plugin(s) not found"),
            ),
            pytest.raises(typer.Exit) as exc,
        ):
            plugins(config=None)

        assert exc.value.exit_code == 1

    def test_plugins_shows_registered_hooks(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Displays hook registrations for loaded plugins."""
        cfg = _make_config(tmp_path, enabled_plugins=["lab"])

        def dummy_handler(_context: object) -> None:
            return None

        manager = HookManager.from_registrations(
            [
                HookRegistration(
                    plugin="lab",
                    hook="sync",
                    event=HookEvent.PRE_APPLY,
                    handler=dummy_handler,
                    policy=HookPolicy.WARN,
                )
            ]
        )

        with (
            patch("compose_farm.cli.management.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.management.list_available_plugins", return_value=["lab"]),
            patch("compose_farm.cli.management.HookManager.from_config", return_value=manager),
        ):
            plugins(config=None)

        captured = capsys.readouterr()
        assert "Registered hooks" in captured.out
        assert "event=pre_apply" in captured.out
        assert "policy=warn" in captured.out
