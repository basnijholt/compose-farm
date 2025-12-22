"""Tests for sync command and related functions."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm import executor as executor_module
from compose_farm import state as state_module
from compose_farm.cli import management as cli_management_module
from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult, check_stack_running


@pytest.fixture
def mock_config(tmp_path: Path) -> Config:
    """Create a mock config for testing."""
    compose_dir = tmp_path / "stacks"
    compose_dir.mkdir()

    # Create stack directories with compose files
    for stack in ["plex", "jellyfin", "grafana"]:
        stack_dir = compose_dir / stack
        stack_dir.mkdir()
        (stack_dir / "compose.yaml").write_text(f"# {stack} compose file\n")

    return Config(
        compose_dir=compose_dir,
        hosts={
            "nas01": Host(address="192.168.1.10", user="admin", port=22),
            "nas02": Host(address="192.168.1.11", user="admin", port=22),
        },
        stacks={
            "plex": "nas01",
            "jellyfin": "nas01",
            "grafana": "nas02",
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


class TestCheckStackRunning:
    """Tests for check_stack_running function."""

    @pytest.mark.asyncio
    async def test_stack_running(self, mock_config: Config) -> None:
        """Returns True when stack has running containers."""
        with patch.object(executor_module, "run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = CommandResult(
                stack="plex",
                exit_code=0,
                success=True,
                stdout="abc123\ndef456\n",
            )
            result = await check_stack_running(mock_config, "plex", "nas01")
            assert result is True

    @pytest.mark.asyncio
    async def test_stack_not_running(self, mock_config: Config) -> None:
        """Returns False when stack has no running containers."""
        with patch.object(executor_module, "run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = CommandResult(
                stack="plex",
                exit_code=0,
                success=True,
                stdout="",
            )
            result = await check_stack_running(mock_config, "plex", "nas01")
            assert result is False

    @pytest.mark.asyncio
    async def test_command_failed(self, mock_config: Config) -> None:
        """Returns False when command fails."""
        with patch.object(executor_module, "run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = CommandResult(
                stack="plex",
                exit_code=1,
                success=False,
            )
            result = await check_stack_running(mock_config, "plex", "nas01")
            assert result is False


class TestMergeState:
    """Tests for _merge_state helper function."""

    def test_merge_adds_new_stacks(self) -> None:
        """Merging adds newly discovered stacks to existing state."""
        current: dict[str, str | list[str]] = {"plex": "nas01"}
        discovered: dict[str, str | list[str]] = {"jellyfin": "nas02"}
        removed: list[str] = []

        result = cli_management_module._merge_state(current, discovered, removed)

        assert result == {"plex": "nas01", "jellyfin": "nas02"}

    def test_merge_updates_existing_stacks(self) -> None:
        """Merging updates stacks that changed hosts."""
        current: dict[str, str | list[str]] = {"plex": "nas01", "jellyfin": "nas01"}
        discovered: dict[str, str | list[str]] = {"plex": "nas02"}  # plex moved to nas02
        removed: list[str] = []

        result = cli_management_module._merge_state(current, discovered, removed)

        assert result == {"plex": "nas02", "jellyfin": "nas01"}

    def test_merge_removes_stopped_stacks(self) -> None:
        """Merging removes stacks that were checked but not found."""
        current: dict[str, str | list[str]] = {
            "plex": "nas01",
            "jellyfin": "nas01",
            "grafana": "nas02",
        }
        discovered: dict[str, str | list[str]] = {"plex": "nas01"}  # only plex still running
        removed = ["jellyfin"]  # jellyfin was checked and not found

        result = cli_management_module._merge_state(current, discovered, removed)

        # jellyfin removed, grafana untouched (wasn't in the refresh scope)
        assert result == {"plex": "nas01", "grafana": "nas02"}

    def test_merge_preserves_unrelated_stacks(self) -> None:
        """Merging preserves stacks that weren't part of the refresh."""
        current: dict[str, str | list[str]] = {
            "plex": "nas01",
            "jellyfin": "nas01",
            "grafana": "nas02",
        }
        discovered: dict[str, str | list[str]] = {"plex": "nas02"}  # only refreshed plex
        removed: list[str] = []  # nothing was removed

        result = cli_management_module._merge_state(current, discovered, removed)

        # plex updated, others preserved
        assert result == {"plex": "nas02", "jellyfin": "nas01", "grafana": "nas02"}


class TestReportSyncChanges:
    """Tests for _report_sync_changes function."""

    def test_reports_added(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Reports newly discovered stacks."""
        cli_management_module._report_sync_changes(
            added=["plex", "jellyfin"],
            removed=[],
            changed=[],
            discovered={"plex": "nas01", "jellyfin": "nas02"},
            current_state={},
        )
        captured = capsys.readouterr()
        assert "New stacks found (2)" in captured.out
        assert "+ plex on nas01" in captured.out
        assert "+ jellyfin on nas02" in captured.out

    def test_reports_removed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Reports stacks that are no longer running."""
        cli_management_module._report_sync_changes(
            added=[],
            removed=["grafana"],
            changed=[],
            discovered={},
            current_state={"grafana": "nas01"},
        )
        captured = capsys.readouterr()
        assert "Stacks no longer running (1)" in captured.out
        assert "- grafana (was on nas01)" in captured.out

    def test_reports_changed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Reports stacks that moved to a different host."""
        cli_management_module._report_sync_changes(
            added=[],
            removed=[],
            changed=[("plex", "nas01", "nas02")],
            discovered={"plex": "nas02"},
            current_state={"plex": "nas01"},
        )
        captured = capsys.readouterr()
        assert "Stacks on different hosts (1)" in captured.out
        assert "~ plex: nas01 → nas02" in captured.out


class TestRefreshCommand:
    """Tests for the refresh command with stack arguments."""

    def test_refresh_specific_stack_partial_merge(
        self, mock_config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Refreshing specific stacks merges with existing state."""
        # Mock existing state
        existing_state = {"plex": "nas01", "jellyfin": "nas01", "grafana": "nas02"}

        with (
            patch(
                "compose_farm.cli.management.get_stacks",
                return_value=(["plex"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_stacks_full",
                return_value=({"plex": "nas02"}, {}, {}),  # plex moved to nas02
            ),
            patch("compose_farm.cli.management._snapshot_stacks"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            # stacks=["plex"], all_stacks=False -> partial refresh
            cli_management_module.refresh(
                stacks=["plex"],
                all_stacks=False,
                config=None,
                log_path=None,
                dry_run=False,
            )

            # Should have merged: plex updated, others preserved
            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            assert saved_state == {"plex": "nas02", "jellyfin": "nas01", "grafana": "nas02"}

    def test_refresh_all_replaces_state(
        self, mock_config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Refreshing all stacks replaces the entire state."""
        existing_state = {"plex": "nas01", "jellyfin": "nas01", "old-service": "nas02"}

        with (
            patch(
                "compose_farm.cli.management.get_stacks",
                return_value=(["plex", "jellyfin", "grafana"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_stacks_full",
                return_value=(
                    {"plex": "nas01", "grafana": "nas02"},
                    {},
                    {},
                ),  # jellyfin not running
            ),
            patch("compose_farm.cli.management._snapshot_stacks"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            # stacks=None, all_stacks=False -> defaults to all (full refresh)
            cli_management_module.refresh(
                stacks=None,
                all_stacks=False,
                config=None,
                log_path=None,
                dry_run=False,
            )

            # Should have replaced: only discovered stacks remain
            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            assert saved_state == {"plex": "nas01", "grafana": "nas02"}

    def test_refresh_with_all_flag_full_refresh(self, mock_config: Config) -> None:
        """Using --all flag forces full refresh even with stack names."""
        existing_state = {"plex": "nas01", "jellyfin": "nas01"}

        with (
            patch(
                "compose_farm.cli.management.get_stacks",
                return_value=(["plex", "jellyfin", "grafana"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_stacks_full",
                return_value=({"plex": "nas01"}, {}, {}),  # only plex running
            ),
            patch("compose_farm.cli.management._snapshot_stacks"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            # all_stacks=True -> full refresh (replaces state)
            cli_management_module.refresh(
                stacks=["plex"],  # ignored when --all is set
                all_stacks=True,
                config=None,
                log_path=None,
                dry_run=False,
            )

            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            # Full refresh: only discovered stacks
            assert saved_state == {"plex": "nas01"}

    def test_refresh_partial_removes_stopped_stack(self, mock_config: Config) -> None:
        """Partial refresh removes a stack if it was checked but not found."""
        existing_state = {"plex": "nas01", "jellyfin": "nas01", "grafana": "nas02"}

        with (
            patch(
                "compose_farm.cli.management.get_stacks",
                return_value=(["plex", "jellyfin"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_stacks_full",
                return_value=({"plex": "nas01"}, {}, {}),  # jellyfin not running
            ),
            patch("compose_farm.cli.management._snapshot_stacks"),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            cli_management_module.refresh(
                stacks=["plex", "jellyfin"],
                all_stacks=False,
                config=None,
                log_path=None,
                dry_run=False,
            )

            mock_save.assert_called_once()
            saved_state = mock_save.call_args[0][1]
            # jellyfin removed (was checked), grafana preserved (wasn't checked)
            assert saved_state == {"plex": "nas01", "grafana": "nas02"}

    def test_refresh_dry_run_no_state_change(
        self, mock_config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Dry run shows changes but doesn't modify state."""
        existing_state = {"plex": "nas01"}

        with (
            patch(
                "compose_farm.cli.management.get_stacks",
                return_value=(["plex"], mock_config),
            ),
            patch(
                "compose_farm.cli.management.load_state",
                return_value=existing_state,
            ),
            patch(
                "compose_farm.cli.management._discover_stacks_full",
                return_value=({"plex": "nas02"}, {}, {}),  # would change
            ),
            patch("compose_farm.cli.management.save_state") as mock_save,
        ):
            cli_management_module.refresh(
                stacks=["plex"],
                all_stacks=False,
                config=None,
                log_path=None,
                dry_run=True,
            )

            # Should not save state in dry run
            mock_save.assert_not_called()

            captured = capsys.readouterr()
            assert "dry-run" in captured.out
