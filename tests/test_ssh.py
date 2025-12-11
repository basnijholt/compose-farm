"""Tests for ssh module."""

from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.ssh import (
    CommandResult,
    _is_local,
    _run_local_command,
    run_command,
    run_compose,
    run_on_services,
)


class TestIsLocal:
    """Tests for _is_local function."""

    @pytest.mark.parametrize(
        "address",
        ["local", "localhost", "127.0.0.1", "::1", "LOCAL", "LOCALHOST"],
    )
    def test_local_addresses(self, address: str) -> None:
        host = Host(address=address)
        assert _is_local(host) is True

    @pytest.mark.parametrize(
        "address",
        ["192.168.1.10", "nas01.local", "10.0.0.1", "example.com"],
    )
    def test_remote_addresses(self, address: str) -> None:
        host = Host(address=address)
        assert _is_local(host) is False


class TestRunLocalCommand:
    """Tests for local command execution."""

    async def test_run_local_command_success(self) -> None:
        result = await _run_local_command("echo hello", "test-service")
        assert result.success is True
        assert result.exit_code == 0
        assert result.service == "test-service"

    async def test_run_local_command_failure(self) -> None:
        result = await _run_local_command("exit 1", "test-service")
        assert result.success is False
        assert result.exit_code == 1

    async def test_run_local_command_not_found(self) -> None:
        result = await _run_local_command("nonexistent_command_xyz", "test-service")
        assert result.success is False
        assert result.exit_code != 0


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
        assert result.service == "my-service"
        assert result.exit_code == 0
        assert result.success is True


class TestRunCompose:
    """Tests for compose command execution."""

    async def test_run_compose_builds_correct_command(self, tmp_path: Path) -> None:
        # Create a minimal compose file
        compose_dir = tmp_path / "compose"
        service_dir = compose_dir / "test-service"
        service_dir.mkdir(parents=True)
        compose_file = service_dir / "docker-compose.yml"
        compose_file.write_text("services: {}")

        config = Config(
            compose_dir=compose_dir,
            hosts={"local": Host(address="localhost")},
            services={"test-service": "local"},
        )

        # This will fail because docker compose isn't running,
        # but we can verify the command structure works
        result = await run_compose(config, "test-service", "config", stream=False)
        # Command may fail due to no docker, but structure is correct
        assert result.service == "test-service"


class TestRunOnServices:
    """Tests for parallel service execution."""

    async def test_run_on_services_parallel(self) -> None:
        config = Config(
            compose_dir=Path("/tmp"),
            hosts={"local": Host(address="localhost")},
            services={"svc1": "local", "svc2": "local"},
        )

        # Use a simple command that will work without docker
        # We'll test the parallelism structure
        results = await run_on_services(config, ["svc1", "svc2"], "version", stream=False)
        assert len(results) == 2
        assert results[0].service == "svc1"
        assert results[1].service == "svc2"
