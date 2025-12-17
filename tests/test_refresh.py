"""Tests for sync command and related functions."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm import executor as executor_module
from compose_farm import state as state_module
from compose_farm.cli import management as cli_management_module
from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult, check_service_running


@pytest.fixture
def mock_config(tmp_path: Path) -> Config:
    """Create a mock config for testing."""
    compose_dir = tmp_path / "stacks"
    compose_dir.mkdir()

    # Create service directories with compose files
    for service in ["plex", "jellyfin", "sonarr"]:
        svc_dir = compose_dir / service
        svc_dir.mkdir()
        (svc_dir / "compose.yaml").write_text(f"# {service} compose file\n")

    return Config(
        compose_dir=compose_dir,
        hosts={
            "nas01": Host(address="192.168.1.10", user="admin", port=22),
            "nas02": Host(address="192.168.1.11", user="admin", port=22),
        },
        services={
            "plex": "nas01",
            "jellyfin": "nas01",
            "sonarr": "nas02",
        },
    )


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary state directory and patch _get_state_path."""
    state_path = tmp_path / ".config" / "compose-farm"
    state_path.mkdir(parents=True)

    def mock_get_state_path() -> Path:
        return state_path / "state.yaml"

    monkeypatch.setattr(state_module, "_get_state_path", mock_get_state_path)
    return state_path


class TestCheckServiceRunning:
    """Tests for check_service_running function."""

    @pytest.mark.asyncio
    async def test_service_running(self, mock_config: Config) -> None:
        """Returns True when service has running containers."""
        with patch.object(executor_module, "run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = CommandResult(
                service="plex",
                exit_code=0,
                success=True,
                stdout="abc123\ndef456\n",
            )
            result = await check_service_running(mock_config, "plex", "nas01")
            assert result is True

    @pytest.mark.asyncio
    async def test_service_not_running(self, mock_config: Config) -> None:
        """Returns False when service has no running containers."""
        with patch.object(executor_module, "run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = CommandResult(
                service="plex",
                exit_code=0,
                success=True,
                stdout="",
            )
            result = await check_service_running(mock_config, "plex", "nas01")
            assert result is False

    @pytest.mark.asyncio
    async def test_command_failed(self, mock_config: Config) -> None:
        """Returns False when command fails."""
        with patch.object(executor_module, "run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = CommandResult(
                service="plex",
                exit_code=1,
                success=False,
            )
            result = await check_service_running(mock_config, "plex", "nas01")
            assert result is False


class TestReportSyncChanges:
    """Tests for _report_sync_changes function."""

    def test_reports_added(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Reports newly discovered services."""
        cli_management_module._report_sync_changes(
            added=["plex", "jellyfin"],
            removed=[],
            changed=[],
            discovered={"plex": "nas01", "jellyfin": "nas02"},
            current_state={},
        )
        captured = capsys.readouterr()
        assert "New services found (2)" in captured.out
        assert "+ plex on nas01" in captured.out
        assert "+ jellyfin on nas02" in captured.out

    def test_reports_removed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Reports services that are no longer running."""
        cli_management_module._report_sync_changes(
            added=[],
            removed=["sonarr"],
            changed=[],
            discovered={},
            current_state={"sonarr": "nas01"},
        )
        captured = capsys.readouterr()
        assert "Services no longer running (1)" in captured.out
        assert "- sonarr (was on nas01)" in captured.out

    def test_reports_changed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Reports services that moved to a different host."""
        cli_management_module._report_sync_changes(
            added=[],
            removed=[],
            changed=[("plex", "nas01", "nas02")],
            discovered={"plex": "nas02"},
            current_state={"plex": "nas01"},
        )
        captured = capsys.readouterr()
        assert "Services on different hosts (1)" in captured.out
        assert "~ plex: nas01 → nas02" in captured.out
