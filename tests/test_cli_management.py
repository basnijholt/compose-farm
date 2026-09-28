"""Tests for CLI management helpers."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm.cli import management
from compose_farm.config import Config, Host
from compose_farm.operations import PreflightResult
from tests.plugin_helpers import make_config


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


def test_check_stack_requirements_includes_plugin_errors(tmp_path: Path) -> None:
    """Plugin preflight problems are reported with the other preflight failures."""
    config = Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="localhost")},
        stacks={"svc": "host1"},
    )
    preflight = PreflightResult([], [], [], [], ("plugin zfs: pool tank missing",))

    with patch(
        "compose_farm.cli.management.check_stack_requirements",
        new_callable=AsyncMock,
        return_value=preflight,
    ):
        *_, preflight_errors = management._check_stack_requirements(config, ["svc"])

    assert preflight_errors == [("svc", "host1", "plugin zfs: pool tank missing")]


def test_check_lists_enabled_plugins(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Cf check names the enabled plugins."""
    cfg = make_config(tmp_path, {"svc": "h1"})
    cfg.plugins = {"sync": None}
    cfg._loaded_plugins = ()

    with (
        patch("compose_farm.cli.management.load_config_or_exit", return_value=cfg),
        patch("compose_farm.cli.management._report_orphaned_stacks", return_value=False),
    ):
        management.check(stacks=None, local=True, config=None)

    assert "Plugins: sync" in capsys.readouterr().out
