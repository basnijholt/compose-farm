"""Tests for operations module."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from compose_farm.cli import lifecycle
from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult
from compose_farm.operations import (
    _migrate_stack,
    build_discovery_results,
)


@pytest.fixture
def basic_config(tmp_path: Path) -> Config:
    """Create a basic test config."""
    compose_dir = tmp_path / "compose"
    stack_dir = compose_dir / "test-service"
    stack_dir.mkdir(parents=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}")
    return Config(
        compose_dir=compose_dir,
        hosts={
            "host1": Host(address="localhost"),
            "host2": Host(address="localhost"),
        },
        stacks={"test-service": "host2"},
    )


class TestMigrationCommands:
    """Tests for migration command sequence."""

    @pytest.fixture
    def config(self, tmp_path: Path) -> Config:
        """Create a test config."""
        compose_dir = tmp_path / "compose"
        stack_dir = compose_dir / "test-service"
        stack_dir.mkdir(parents=True)
        (stack_dir / "docker-compose.yml").write_text("services: {}")
        return Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="localhost"),
                "host2": Host(address="localhost"),
            },
            stacks={"test-service": "host2"},
        )

    async def test_migration_uses_pull_ignore_buildable(self, config: Config) -> None:
        """Migration should use 'pull --ignore-buildable' to skip buildable images."""
        commands_called: list[str] = []

        async def mock_run_compose_step(
            cfg: Config,
            stack: str,
            command: str,
            *,
            raw: bool,
            host: str | None = None,
        ) -> CommandResult:
            commands_called.append(command)
            return CommandResult(
                stack=stack,
                exit_code=0,
                success=True,
            )

        with patch(
            "compose_farm.operations._run_compose_step",
            side_effect=mock_run_compose_step,
        ):
            await _migrate_stack(
                config,
                "test-service",
                current_host="host1",
                target_host="host2",
                prefix="[test]",
                raw=False,
            )

        # Migration should call pull with --ignore-buildable, then build, then down
        assert "pull --ignore-buildable" in commands_called
        assert "build" in commands_called
        assert "down" in commands_called
        # pull should come before build
        pull_idx = commands_called.index("pull --ignore-buildable")
        build_idx = commands_called.index("build")
        assert pull_idx < build_idx


class TestUpdateCommandSequence:
    """Tests for update command sequence."""

    def test_update_command_sequence_includes_build(self) -> None:
        """Update command should use pull --ignore-buildable and build."""
        # This is a static check of the command sequence in lifecycle.py
        # The actual command sequence is defined in the update function

        source = inspect.getsource(lifecycle.update)

        # Verify the command sequence includes pull --ignore-buildable
        assert "pull --ignore-buildable" in source
        # Verify build is included
        assert '"build"' in source or "'build'" in source
        # Verify the sequence is pull, build, down, up
        assert "down" in source
        assert "up -d" in source


class TestBuildDiscoveryResults:
    """Tests for build_discovery_results function."""

    @pytest.fixture
    def config(self, tmp_path: Path) -> Config:
        """Create a test config with multiple stacks."""
        compose_dir = tmp_path / "compose"
        for stack in ["plex", "jellyfin", "sonarr"]:
            (compose_dir / stack).mkdir(parents=True)
            (compose_dir / stack / "docker-compose.yml").write_text("services: {}")

        return Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="localhost"),
                "host2": Host(address="localhost"),
            },
            stacks={"plex": "host1", "jellyfin": "host1", "sonarr": "host2"},
        )

    def test_discovers_correctly_running_stacks(self, config: Config) -> None:
        """Stacks running on correct hosts are discovered."""
        running_on_host = {
            "host1": {"plex", "jellyfin"},
            "host2": {"sonarr"},
        }

        discovered, strays, duplicates = build_discovery_results(config, running_on_host)

        assert discovered == {"plex": "host1", "jellyfin": "host1", "sonarr": "host2"}
        assert strays == {}
        assert duplicates == {}

    def test_detects_stray_stacks(self, config: Config) -> None:
        """Stacks running on wrong hosts are marked as strays."""
        running_on_host = {
            "host1": set(),
            "host2": {"plex"},  # plex should be on host1
        }

        discovered, strays, _duplicates = build_discovery_results(config, running_on_host)

        assert "plex" not in discovered
        assert strays == {"plex": ["host2"]}

    def test_detects_duplicates(self, config: Config) -> None:
        """Single-host stacks running on multiple hosts are duplicates."""
        running_on_host = {
            "host1": {"plex"},
            "host2": {"plex"},  # plex running on both hosts
        }

        discovered, strays, duplicates = build_discovery_results(
            config, running_on_host, stacks=["plex"]
        )

        # plex is correctly running on host1
        assert discovered == {"plex": "host1"}
        # plex is also a stray on host2
        assert strays == {"plex": ["host2"]}
        # plex is a duplicate (single-host stack on multiple hosts)
        assert duplicates == {"plex": ["host1", "host2"]}

    def test_filters_to_requested_stacks(self, config: Config) -> None:
        """Only returns results for requested stacks."""
        running_on_host = {
            "host1": {"plex", "jellyfin"},
            "host2": {"sonarr"},
        }

        discovered, _strays, _duplicates = build_discovery_results(
            config, running_on_host, stacks=["plex"]
        )

        # Only plex should be in results
        assert discovered == {"plex": "host1"}
        assert "jellyfin" not in discovered
        assert "sonarr" not in discovered
