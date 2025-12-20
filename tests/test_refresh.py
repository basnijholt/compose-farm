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


class TestMergeState:
    """Tests for _merge_state helper function."""

    def test_merge_adds_new_services(self) -> None:
        """Merging adds newly discovered services to existing state."""
        current: dict[str, str | list[str]] = {"plex": "nas01"}
        discovered: dict[str, str | list[str]] = {"jellyfin": "nas02"}
        removed: list[str] = []

        result = cli_management_module._merge_state(current, discovered, removed)

        assert result == {"plex": "nas01", "jellyfin": "nas02"}

    def test_merge_updates_existing_services(self) -> None:
        """Merging updates services that changed hosts."""
        current: dict[str, str | list[str]] = {"plex": "nas01", "jellyfin": "nas01"}
        discovered: dict[str, str | list[str]] = {"plex": "nas02"}  # plex moved to nas02
        removed: list[str] = []

        result = cli_management_module._merge_state(current, discovered, removed)

        assert result == {"plex": "nas02", "jellyfin": "nas01"}

    def test_merge_removes_stopped_services(self) -> None:
        """Merging removes services that were checked but not found."""
        current: dict[str, str | list[str]] = {
            "plex": "nas01",
            "jellyfin": "nas01",
            "sonarr": "nas02",
        }
        discovered: dict[str, str | list[str]] = {"plex": "nas01"}  # only plex still running
        removed = ["jellyfin"]  # jellyfin was checked and not found

        result = cli_management_module._merge_state(current, discovered, removed)

        # jellyfin removed, sonarr untouched (wasn't in the refresh scope)
        assert result == {"plex": "nas01", "sonarr": "nas02"}

    def test_merge_preserves_unrelated_services(self) -> None:
        """Merging preserves services that weren't part of the refresh."""
        current: dict[str, str | list[str]] = {
            "plex": "nas01",
            "jellyfin": "nas01",
            "sonarr": "nas02",
        }
        discovered: dict[str, str | list[str]] = {"plex": "nas02"}  # only refreshed plex
        removed: list[str] = []  # nothing was removed

        result = cli_management_module._merge_state(current, discovered, removed)

        # plex updated, others preserved
        assert result == {"plex": "nas02", "jellyfin": "nas01", "sonarr": "nas02"}


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


class TestRefreshCommand:
    """Tests for the refresh command with service arguments."""

    def test_refresh_specific_service_partial_merge(
        self, mock_config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Refreshing specific services merges with existing state."""
        # Mock existing state
        existing_state = {"plex": "nas01", "jellyfin": "nas01", "sonarr": "nas02"}

        with (
            patch(
                "compose_farm.cli.management.get_services",
                return_value=(["plex"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_services",
                return_value={"plex": "nas02"},  # plex moved to nas02
            ),
            patch("compose_farm.cli.management._snapshot_services"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            # services=["plex"], all_services=False -> partial refresh
            cli_management_module.refresh(
                services=["plex"],
                all_services=False,
                config=None,
                log_path=None,
                dry_run=False,
            )

            # Should have merged: plex updated, others preserved
            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            assert saved_state == {"plex": "nas02", "jellyfin": "nas01", "sonarr": "nas02"}

    def test_refresh_all_replaces_state(
        self, mock_config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Refreshing all services replaces the entire state."""
        existing_state = {"plex": "nas01", "jellyfin": "nas01", "old-service": "nas02"}

        with (
            patch(
                "compose_farm.cli.management.get_services",
                return_value=(["plex", "jellyfin", "sonarr"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_services",
                return_value={"plex": "nas01", "sonarr": "nas02"},  # jellyfin not running
            ),
            patch("compose_farm.cli.management._snapshot_services"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            # services=None, all_services=False -> defaults to all (full refresh)
            cli_management_module.refresh(
                services=None,
                all_services=False,
                config=None,
                log_path=None,
                dry_run=False,
            )

            # Should have replaced: only discovered services remain
            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            assert saved_state == {"plex": "nas01", "sonarr": "nas02"}

    def test_refresh_with_all_flag_full_refresh(self, mock_config: Config) -> None:
        """Using --all flag forces full refresh even with service names."""
        existing_state = {"plex": "nas01", "jellyfin": "nas01"}

        with (
            patch(
                "compose_farm.cli.management.get_services",
                return_value=(["plex", "jellyfin", "sonarr"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_services",
                return_value={"plex": "nas01"},  # only plex running
            ),
            patch("compose_farm.cli.management._snapshot_services"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            # all_services=True -> full refresh (replaces state)
            cli_management_module.refresh(
                services=["plex"],  # ignored when --all is set
                all_services=True,
                config=None,
                log_path=None,
                dry_run=False,
            )

            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            # Full refresh: only discovered services
            assert saved_state == {"plex": "nas01"}

    def test_refresh_partial_removes_stopped_service(self, mock_config: Config) -> None:
        """Partial refresh removes a service if it was checked but not found."""
        existing_state = {"plex": "nas01", "jellyfin": "nas01", "sonarr": "nas02"}

        with (
            patch(
                "compose_farm.cli.management.get_services",
                return_value=(["plex", "jellyfin"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_services",
                return_value={"plex": "nas01"},  # jellyfin not running
            ),
            patch("compose_farm.cli.management._snapshot_services"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            cli_management_module.refresh(
                services=["plex", "jellyfin"],
                all_services=False,
                config=None,
                log_path=None,
                dry_run=False,
            )

            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            # jellyfin removed (was checked), sonarr preserved (wasn't checked)
            assert saved_state == {"plex": "nas01", "sonarr": "nas02"}

    def test_refresh_dry_run_no_state_change(
        self, mock_config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Dry run shows changes but doesn't modify state."""
        existing_state = {"plex": "nas01"}

        with (
            patch(
                "compose_farm.cli.management.get_services",
                return_value=(["plex"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_services",
                return_value={"plex": "nas02"},  # would change
            ),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            cli_management_module.refresh(
                services=["plex"],
                all_services=False,
                config=None,
                log_path=None,
                dry_run=True,
            )

            # Should not save state in dry run
            mock_save.assert_not_called()

            captured = capsys.readouterr()
            assert "dry-run" in captured.out
