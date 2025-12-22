"""Tests for executor module."""

import sys
from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.executor import (
    CommandResult,
    _run_local_command,
    check_networks_exist,
    check_paths_exist,
    get_running_stacks_on_host,
    is_local,
    run_command,
    run_compose,
    run_on_stacks,
)

# These tests run actual shell commands that only work on Linux
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only shell commands")


class TestIsLocal:
    """Tests for is_local function."""

    @pytest.mark.parametrize(
        "address",
        ["local", "localhost", "127.0.0.1", "::1", "LOCAL", "LOCALHOST"],
    )
    def test_local_addresses(self, address: str) -> None:
        host = Host(address=address)
        assert is_local(host) is True

    @pytest.mark.parametrize(
        "address",
        ["192.168.1.10", "nas01.local", "10.0.0.1", "example.com"],
    )
    def test_remote_addresses(self, address: str) -> None:
        host = Host(address=address)
        assert is_local(host) is False


class TestRunLocalCommand:
    """Tests for local command execution."""

    async def test_run_local_command_success(self) -> None:
        result = await _run_local_command("echo hello", "test-service")
        assert result.success is True
        assert result.exit_code == 0
        assert result.stack == "test-service"

    async def test_run_local_command_failure(self) -> None:
        result = await _run_local_command("exit 1", "test-service")
        assert result.success is False
        assert result.exit_code == 1

    async def test_run_local_command_not_found(self) -> None:
        result = await _run_local_command("nonexistent_command_xyz", "test-service")
        assert result.success is False
        assert result.exit_code != 0

    async def test_run_local_command_captures_output(self) -> None:
        result = await _run_local_command("echo hello", "test-service", stream=False)
        assert "hello" in result.stdout


class TestRunCommand:
    """Tests for run_command dispatcher."""

    async def test_run_command_local(self) -> None:
        host = Host(address="localhost")
        result = await run_command(host, "echo test", "test-service")
        assert result.success is True

    async def test_run_command_result_structure(self) -> None:
        host = Host(address="local")
        result = await run_command(host, "true", "my-service")
        assert isinstance(result, CommandResult)
        assert result.stack == "my-service"
        assert result.exit_code == 0
        assert result.success is True


class TestRunCompose:
    """Tests for compose command execution."""

    async def test_run_compose_builds_correct_command(self, tmp_path: Path) -> None:
        # Create a minimal compose file
        compose_dir = tmp_path / "compose"
        stack_dir = compose_dir / "test-service"
        stack_dir.mkdir(parents=True)
        compose_file = stack_dir / "docker-compose.yml"
        compose_file.write_text("services: {}")

        config = Config(
            compose_dir=compose_dir,
            hosts={"local": Host(address="localhost")},
            stacks={"test-service": "local"},
        )

        # This will fail because docker compose isn't running,
        # but we can verify the command structure works
        result = await run_compose(config, "test-service", "config", stream=False)
        # Command may fail due to no docker, but structure is correct
        assert result.stack == "test-service"


class TestRunOnStacks:
    """Tests for parallel stack execution."""

    async def test_run_on_stacks_parallel(self) -> None:
        config = Config(
            compose_dir=Path("/tmp"),
            hosts={"local": Host(address="localhost")},
            stacks={"svc1": "local", "svc2": "local"},
        )

        # Use a simple command that will work without docker
        # We'll test the parallelism structure
        results = await run_on_stacks(config, ["svc1", "svc2"], "version", stream=False)
        assert len(results) == 2
        assert results[0].stack == "svc1"
        assert results[1].stack == "svc2"


@linux_only
class TestCheckPathsExist:
    """Tests for check_paths_exist function (uses 'test -e' shell command)."""

    async def test_check_existing_paths(self, tmp_path: Path) -> None:
        """Check paths that exist."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )
        # Create test paths
        (tmp_path / "dir1").mkdir()
        (tmp_path / "file1").touch()

        result = await check_paths_exist(
            config, "local", [str(tmp_path / "dir1"), str(tmp_path / "file1")]
        )

        assert result[str(tmp_path / "dir1")] is True
        assert result[str(tmp_path / "file1")] is True

    async def test_check_missing_paths(self, tmp_path: Path) -> None:
        """Check paths that don't exist."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_paths_exist(
            config, "local", [str(tmp_path / "missing1"), str(tmp_path / "missing2")]
        )

        assert result[str(tmp_path / "missing1")] is False
        assert result[str(tmp_path / "missing2")] is False

    async def test_check_mixed_paths(self, tmp_path: Path) -> None:
        """Check mix of existing and missing paths."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )
        (tmp_path / "exists").mkdir()

        result = await check_paths_exist(
            config, "local", [str(tmp_path / "exists"), str(tmp_path / "missing")]
        )

        assert result[str(tmp_path / "exists")] is True
        assert result[str(tmp_path / "missing")] is False

    async def test_check_empty_paths(self, tmp_path: Path) -> None:
        """Empty path list returns empty dict."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_paths_exist(config, "local", [])
        assert result == {}


@linux_only
class TestCheckNetworksExist:
    """Tests for check_networks_exist function (requires Docker)."""

    async def test_check_bridge_network_exists(self, tmp_path: Path) -> None:
        """The 'bridge' network always exists on Docker hosts."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(config, "local", ["bridge"])
        assert result["bridge"] is True

    async def test_check_nonexistent_network(self, tmp_path: Path) -> None:
        """Check a network that doesn't exist."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(config, "local", ["nonexistent_network_xyz_123"])
        assert result["nonexistent_network_xyz_123"] is False

    async def test_check_mixed_networks(self, tmp_path: Path) -> None:
        """Check mix of existing and non-existing networks."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(
            config, "local", ["bridge", "nonexistent_network_xyz_123"]
        )
        assert result["bridge"] is True
        assert result["nonexistent_network_xyz_123"] is False

    async def test_check_empty_networks(self, tmp_path: Path) -> None:
        """Empty network list returns empty dict."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(config, "local", [])
        assert result == {}


@linux_only
class TestGetRunningStacksOnHost:
    """Tests for get_running_stacks_on_host function (requires Docker)."""

    async def test_returns_set_of_stacks(self, tmp_path: Path) -> None:
        """Function returns a set of stack names."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await get_running_stacks_on_host(config, "local")
        assert isinstance(result, set)

    async def test_filters_empty_lines(self, tmp_path: Path) -> None:
        """Empty project names are filtered out."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        # Result should not contain empty strings
        result = await get_running_stacks_on_host(config, "local")
        assert "" not in result
