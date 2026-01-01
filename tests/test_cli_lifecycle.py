"""Tests for CLI lifecycle commands (apply, down --orphaned)."""

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from compose_farm.cli.lifecycle import apply, down
from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult


def _make_config(tmp_path: Path, stacks: dict[str, str] | None = None) -> Config:
    """Create a minimal config for testing."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()

    stack_dict: dict[str, str | list[str]] = (
        dict(stacks) if stacks else {"svc1": "host1", "svc2": "host2"}
    )
    for stack in stack_dict:
        stack_dir = compose_dir / stack
        stack_dir.mkdir()
        (stack_dir / "docker-compose.yml").write_text("services: {}\n")

    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text("")

    return Config(
        compose_dir=compose_dir,
        hosts={"host1": Host(address="localhost"), "host2": Host(address="localhost")},
        stacks=stack_dict,
        config_path=config_path,
    )


def _make_result(stack: str, success: bool = True) -> CommandResult:
    """Create a command result."""
    return CommandResult(
        stack=stack,
        exit_code=0 if success else 1,
        success=success,
        stdout="",
        stderr="",
    )


class TestApplyCommand:
    """Tests for the apply command."""

    def test_apply_nothing_to_do(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """When no migrations, orphans, or missing stacks, prints success message."""
        cfg = _make_config(tmp_path)

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
        ):
            apply(dry_run=False, no_orphans=False, no_strays=False, full=False, config=None)

        captured = capsys.readouterr()
        assert "Nothing to apply" in captured.out

    def test_apply_dry_run_shows_preview(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Dry run shows what would be done without executing."""
        cfg = _make_config(tmp_path)

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.cli.lifecycle.get_orphaned_stacks",
                return_value={"old-svc": "host1"},
            ),
            patch(
                "compose_farm.cli.lifecycle.get_stacks_needing_migration",
                return_value=["svc1"],
            ),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stack_host", return_value="host1"),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch("compose_farm.cli.lifecycle.stop_orphaned_stacks") as mock_stop,
            patch("compose_farm.cli.lifecycle.up_stacks") as mock_up,
        ):
            apply(dry_run=True, no_orphans=False, no_strays=False, full=False, config=None)

        captured = capsys.readouterr()
        assert "Stacks to migrate" in captured.out
        assert "svc1" in captured.out
        assert "Orphaned stacks to stop" in captured.out
        assert "old-svc" in captured.out
        assert "dry-run" in captured.out

        # Should not have called the actual operations
        mock_stop.assert_not_called()
        mock_up.assert_not_called()

    def test_apply_executes_migrations(self, tmp_path: Path) -> None:
        """Apply runs migrations when stacks need migration."""
        cfg = _make_config(tmp_path)
        mock_results = [_make_result("svc1")]

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.get_stacks_needing_migration",
                return_value=["svc1"],
            ),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stack_host", return_value="host1"),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.run_async",
                return_value=mock_results,
            ),
            patch("compose_farm.cli.lifecycle.up_stacks") as mock_up,
            patch("compose_farm.cli.lifecycle.maybe_regenerate_traefik"),
            patch("compose_farm.cli.lifecycle.report_results"),
        ):
            apply(dry_run=False, no_orphans=False, no_strays=False, full=False, config=None)

            mock_up.assert_called_once()
            call_args = mock_up.call_args
            assert call_args[0][1] == ["svc1"]  # stacks list

    def test_apply_executes_orphan_cleanup(self, tmp_path: Path) -> None:
        """Apply stops orphaned stacks."""
        cfg = _make_config(tmp_path)
        mock_results = [_make_result("old-svc@host1")]

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.cli.lifecycle.get_orphaned_stacks",
                return_value={"old-svc": "host1"},
            ),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.run_async",
                return_value=mock_results,
            ),
            patch("compose_farm.cli.lifecycle.stop_orphaned_stacks") as mock_stop,
            patch("compose_farm.cli.lifecycle.report_results"),
        ):
            apply(dry_run=False, no_orphans=False, no_strays=False, full=False, config=None)

            mock_stop.assert_called_once_with(cfg)

    def test_apply_no_orphans_skips_orphan_cleanup(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--no-orphans flag skips orphan cleanup."""
        cfg = _make_config(tmp_path)
        mock_results = [_make_result("svc1")]

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.cli.lifecycle.get_orphaned_stacks",
                return_value={"old-svc": "host1"},
            ),
            patch(
                "compose_farm.cli.lifecycle.get_stacks_needing_migration",
                return_value=["svc1"],
            ),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stack_host", return_value="host1"),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.run_async",
                return_value=mock_results,
            ),
            patch("compose_farm.cli.lifecycle.up_stacks") as mock_up,
            patch("compose_farm.cli.lifecycle.stop_orphaned_stacks") as mock_stop,
            patch("compose_farm.cli.lifecycle.maybe_regenerate_traefik"),
            patch("compose_farm.cli.lifecycle.report_results"),
        ):
            apply(dry_run=False, no_orphans=True, no_strays=False, full=False, config=None)

            # Should run migrations but not orphan cleanup
            mock_up.assert_called_once()
            mock_stop.assert_not_called()

        # Orphans should not appear in output
        captured = capsys.readouterr()
        assert "old-svc" not in captured.out

    def test_apply_no_orphans_nothing_to_do(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--no-orphans with only orphans means nothing to do."""
        cfg = _make_config(tmp_path)

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.cli.lifecycle.get_orphaned_stacks",
                return_value={"old-svc": "host1"},
            ),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
        ):
            apply(dry_run=False, no_orphans=True, no_strays=False, full=False, config=None)

        captured = capsys.readouterr()
        assert "Nothing to apply" in captured.out

    def test_apply_starts_missing_stacks(self, tmp_path: Path) -> None:
        """Apply starts stacks that are in config but not in state."""
        cfg = _make_config(tmp_path)
        mock_results = [_make_result("svc1")]

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch(
                "compose_farm.cli.lifecycle.get_stacks_not_in_state",
                return_value=["svc1"],
            ),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.run_async",
                return_value=mock_results,
            ),
            patch("compose_farm.cli.lifecycle.up_stacks") as mock_up,
            patch("compose_farm.cli.lifecycle.maybe_regenerate_traefik"),
            patch("compose_farm.cli.lifecycle.report_results"),
        ):
            apply(dry_run=False, no_orphans=False, no_strays=False, full=False, config=None)

            mock_up.assert_called_once()
            call_args = mock_up.call_args
            assert call_args[0][1] == ["svc1"]

    def test_apply_dry_run_shows_missing_stacks(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Dry run shows stacks that would be started."""
        cfg = _make_config(tmp_path)

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch(
                "compose_farm.cli.lifecycle.get_stacks_not_in_state",
                return_value=["svc1"],
            ),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
        ):
            apply(dry_run=True, no_orphans=False, no_strays=False, full=False, config=None)

        captured = capsys.readouterr()
        assert "Stacks to start" in captured.out
        assert "svc1" in captured.out
        assert "dry-run" in captured.out

    def test_apply_full_refreshes_all_stacks(self, tmp_path: Path) -> None:
        """--full runs up on all stacks to pick up config changes."""
        cfg = _make_config(tmp_path)
        mock_results = [_make_result("svc1"), _make_result("svc2")]

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.run_async",
                return_value=mock_results,
            ),
            patch("compose_farm.cli.lifecycle.up_stacks") as mock_up,
            patch("compose_farm.cli.lifecycle.maybe_regenerate_traefik"),
            patch("compose_farm.cli.lifecycle.report_results"),
        ):
            apply(dry_run=False, no_orphans=False, no_strays=False, full=True, config=None)

            mock_up.assert_called_once()
            call_args = mock_up.call_args
            # Should refresh all stacks in config
            assert set(call_args[0][1]) == {"svc1", "svc2"}

    def test_apply_full_dry_run_shows_refresh(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--full --dry-run shows stacks that would be refreshed."""
        cfg = _make_config(tmp_path)

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=[]),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
        ):
            apply(dry_run=True, no_orphans=False, no_strays=False, full=True, config=None)

        captured = capsys.readouterr()
        assert "Stacks to refresh" in captured.out
        assert "svc1" in captured.out
        assert "svc2" in captured.out
        assert "dry-run" in captured.out

    def test_apply_full_excludes_already_handled_stacks(self, tmp_path: Path) -> None:
        """--full doesn't double-process stacks that are migrating or starting."""
        cfg = _make_config(tmp_path, {"svc1": "host1", "svc2": "host2", "svc3": "host1"})
        mock_results = [_make_result("svc1"), _make_result("svc3")]

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.get_stacks_needing_migration",
                return_value=["svc1"],
            ),
            patch(
                "compose_farm.cli.lifecycle.get_stacks_not_in_state",
                return_value=["svc2"],
            ),
            patch("compose_farm.cli.lifecycle.get_stack_host", return_value="host2"),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch(
                "compose_farm.cli.lifecycle.run_async",
                return_value=mock_results,
            ),
            patch("compose_farm.cli.lifecycle.up_stacks") as mock_up,
            patch("compose_farm.cli.lifecycle.maybe_regenerate_traefik"),
            patch("compose_farm.cli.lifecycle.report_results"),
        ):
            apply(dry_run=False, no_orphans=False, no_strays=False, full=True, config=None)

            # up_stacks should be called 3 times: migrate, start, refresh
            assert mock_up.call_count == 3
            # Get the third call (refresh) and check it only has svc3
            refresh_call = mock_up.call_args_list[2]
            assert refresh_call[0][1] == ["svc3"]


class TestDownOrphaned:
    """Tests for down --orphaned flag."""

    def test_down_orphaned_no_orphans(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When no orphans exist, prints success message."""
        cfg = _make_config(tmp_path)

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={}),
        ):
            down(
                stacks=None,
                all_stacks=False,
                orphaned=True,
                host=None,
                config=None,
            )

        captured = capsys.readouterr()
        assert "No orphaned stacks to stop" in captured.out

    def test_down_orphaned_stops_stacks(self, tmp_path: Path) -> None:
        """--orphaned stops orphaned stacks."""
        cfg = _make_config(tmp_path)
        mock_results = [_make_result("old-svc@host1")]

        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.cli.lifecycle.get_orphaned_stacks",
                return_value={"old-svc": "host1"},
            ),
            patch(
                "compose_farm.cli.lifecycle.run_async",
                return_value=mock_results,
            ),
            patch("compose_farm.cli.lifecycle.stop_orphaned_stacks") as mock_stop,
            patch("compose_farm.cli.lifecycle.report_results"),
        ):
            down(
                stacks=None,
                all_stacks=False,
                orphaned=True,
                host=None,
                config=None,
            )

            mock_stop.assert_called_once_with(cfg)

    def test_down_orphaned_with_stacks_errors(self) -> None:
        """--orphaned cannot be combined with stack arguments."""
        with pytest.raises(typer.Exit) as exc_info:
            down(
                stacks=["svc1"],
                all_stacks=False,
                orphaned=True,
                host=None,
                config=None,
            )

        assert exc_info.value.exit_code == 1

    def test_down_orphaned_with_all_errors(self) -> None:
        """--orphaned cannot be combined with --all."""
        with pytest.raises(typer.Exit) as exc_info:
            down(
                stacks=None,
                all_stacks=True,
                orphaned=True,
                host=None,
                config=None,
            )

        assert exc_info.value.exit_code == 1

    def test_down_orphaned_with_host_errors(self) -> None:
        """--orphaned cannot be combined with --host."""
        with pytest.raises(typer.Exit) as exc_info:
            down(
                stacks=None,
                all_stacks=False,
                orphaned=True,
                host="host1",
                config=None,
            )

        assert exc_info.value.exit_code == 1
