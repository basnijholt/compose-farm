"""Tests for CLI management helpers."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from compose_farm.cli import management
from compose_farm.config import Config, Host
from compose_farm.operations import PreflightResult


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
