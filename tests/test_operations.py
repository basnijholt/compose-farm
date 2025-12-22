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
    StackDiscoveryResult,
    _migrate_stack,
    discover_all_stacks_on_all_hosts,
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


class TestDiscoverAllStacksOnAllHosts:
    """Tests for discover_all_stacks_on_all_hosts function."""

    async def test_returns_discovery_results_for_all_stacks(self, basic_config: Config) -> None:
        """Function returns StackDiscoveryResult for each stack."""
        with patch(
            "compose_farm.operations.get_running_stacks_on_host",
            return_value={"test-service"},
        ):
            results = await discover_all_stacks_on_all_hosts(basic_config)

        assert len(results) == 1
        assert isinstance(results[0], StackDiscoveryResult)
        assert results[0].stack == "test-service"

    async def test_detects_stray_stacks(self, tmp_path: Path) -> None:
        """Function detects stacks running on wrong hosts."""
        compose_dir = tmp_path / "compose"
        (compose_dir / "plex").mkdir(parents=True)
        (compose_dir / "plex" / "docker-compose.yml").write_text("services: {}")

        config = Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="localhost"),
                "host2": Host(address="localhost"),
            },
            stacks={"plex": "host1"},  # Should run on host1
        )

        # Mock: plex is running on host2 (wrong host)
        async def mock_get_running(cfg: Config, host: str) -> set[str]:
            if host == "host2":
                return {"plex"}
            return set()

        with patch(
            "compose_farm.operations.get_running_stacks_on_host",
            side_effect=mock_get_running,
        ):
            results = await discover_all_stacks_on_all_hosts(config)

        assert len(results) == 1
        assert results[0].stack == "plex"
        assert results[0].running_hosts == ["host2"]
        assert results[0].configured_hosts == ["host1"]
        assert results[0].is_stray is True
        assert results[0].stray_hosts == ["host2"]

    async def test_queries_each_host_once(self, tmp_path: Path) -> None:
        """Function makes exactly one call per host, not per stack."""
        compose_dir = tmp_path / "compose"
        for stack in ["plex", "jellyfin", "sonarr"]:
            (compose_dir / stack).mkdir(parents=True)
            (compose_dir / stack / "docker-compose.yml").write_text("services: {}")

        config = Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="localhost"),
                "host2": Host(address="localhost"),
            },
            stacks={"plex": "host1", "jellyfin": "host1", "sonarr": "host2"},
        )

        call_count = {"count": 0}

        async def mock_get_running(cfg: Config, host: str) -> set[str]:
            call_count["count"] += 1
            return set()

        with patch(
            "compose_farm.operations.get_running_stacks_on_host",
            side_effect=mock_get_running,
        ):
            await discover_all_stacks_on_all_hosts(config)

        # Should call once per host (2), not once per stack (3)
        assert call_count["count"] == 2
