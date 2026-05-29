"""Tests for built-in sync plugin."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from compose_farm.config import Config, Host
from compose_farm.plugins import HookContext, HookEvent, HookResult
from compose_farm.plugins.builtin.sync import SyncPlugin


def _make_cfg(tmp_path: Path) -> Config:
    """Build config with one stack and one local host."""
    source_root = tmp_path / "source"
    (source_root / "svc").mkdir(parents=True)
    (source_root / "svc" / "compose.yaml").write_text("services: {}\n")

    compose_root = tmp_path / "compose"
    compose_root.mkdir()

    cfg_path = tmp_path / "compose-farm.yaml"
    cfg_path.write_text("")

    return Config(
        compose_dir=compose_root,
        hosts={"local": Host(address="localhost")},
        stacks={"svc": "local"},
        config_path=cfg_path,
        plugins=["sync"],
        plugin_config={
            "sync": {
                "source_dir": str(source_root),
                "events": ["pre_up", "pre_apply"],
                "delete": True,
            }
        },
    )


def _handler(
    plugin: SyncPlugin,
) -> Callable[[HookContext], HookResult | None]:
    """Get sync-tree handler from plugin registrations."""
    for registration in plugin.register_hooks():
        if registration.hook == "sync-tree":
            return cast("Callable[[HookContext], HookResult | None]", registration.handler)
    msg = "sync-tree hook not registered"
    raise AssertionError(msg)


def test_sync_plugin_dry_run_pre_up(tmp_path: Path) -> None:
    """Pre-up sync succeeds in dry-run mode without subprocess execution."""
    cfg = _make_cfg(tmp_path)
    plugin = SyncPlugin()

    context = HookContext(
        event=HookEvent.PRE_UP,
        stack="svc",
        config=cfg,
        target_host="local",
        dry_run=True,
    )

    with patch("compose_farm.plugins.builtin.sync.shutil.which", return_value="/usr/bin/rsync"):
        result = _handler(plugin)(context)

    assert result is not None
    assert result.success
    assert "svc@local" in result.message


def test_sync_plugin_missing_source_fails(tmp_path: Path) -> None:
    """Plugin fails when configured source stack directory is missing."""
    cfg = _make_cfg(tmp_path)
    cfg.plugin_config["sync"]["source_dir"] = str(tmp_path / "missing")
    plugin = SyncPlugin()

    context = HookContext(
        event=HookEvent.PRE_UP,
        stack="svc",
        config=cfg,
        target_host="local",
        dry_run=True,
    )

    with patch("compose_farm.plugins.builtin.sync.shutil.which", return_value="/usr/bin/rsync"):
        result = _handler(plugin)(context)

    assert result is not None
    assert not result.success
    assert "source_dir does not exist" in result.message


def test_sync_plugin_runs_rsync_on_pre_apply(tmp_path: Path) -> None:
    """Pre-apply sync runs rsync and succeeds when subprocess returns 0."""
    cfg = _make_cfg(tmp_path)
    plugin = SyncPlugin()

    context = HookContext(
        event=HookEvent.PRE_APPLY,
        stack="*",
        config=cfg,
        dry_run=False,
    )

    with (
        patch("compose_farm.plugins.builtin.sync.shutil.which", return_value="/usr/bin/rsync"),
        patch("compose_farm.plugins.builtin.sync.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        result = _handler(plugin)(context)

    assert result is not None
    assert result.success
    assert mock_run.call_count >= 1


def test_sync_plugin_pre_apply_shows_progress_for_multiple_targets(tmp_path: Path) -> None:
    """Pre-apply sync renders a progress bar when syncing multiple targets."""
    cfg = _make_cfg(tmp_path)
    (tmp_path / "source" / "svc2").mkdir(parents=True)
    (tmp_path / "source" / "svc2" / "compose.yaml").write_text("services: {}\n")
    cfg.stacks["svc2"] = "local"
    plugin = SyncPlugin()

    context = HookContext(
        event=HookEvent.PRE_APPLY,
        stack="*",
        config=cfg,
        dry_run=False,
    )

    progress = MagicMock()
    progress.add_task.return_value = 1
    progress_cm = MagicMock()
    progress_cm.__enter__.return_value = progress
    progress_cm.__exit__.return_value = False

    with (
        patch("compose_farm.plugins.builtin.sync.shutil.which", return_value="/usr/bin/rsync"),
        patch("compose_farm.plugins.builtin.sync.Progress", return_value=progress_cm),
        patch("compose_farm.plugins.builtin.sync._ensure_destination_dir", return_value=True),
        patch("compose_farm.plugins.builtin.sync._run_rsync", return_value=True),
    ):
        result = _handler(plugin)(context)

    assert result is not None
    assert result.success
    progress.add_task.assert_called_once_with("Syncing stacks", total=2)
    assert progress.update.call_count == 2
    assert progress.advance.call_count == 2


def test_sync_plugin_pre_up_does_not_show_progress_for_single_target(tmp_path: Path) -> None:
    """Pre-up sync skips the progress bar for a single target to reduce noise."""
    cfg = _make_cfg(tmp_path)
    plugin = SyncPlugin()

    context = HookContext(
        event=HookEvent.PRE_UP,
        stack="svc",
        config=cfg,
        target_host="local",
        dry_run=False,
    )

    with (
        patch("compose_farm.plugins.builtin.sync.shutil.which", return_value="/usr/bin/rsync"),
        patch("compose_farm.plugins.builtin.sync.Progress") as mock_progress,
        patch("compose_farm.plugins.builtin.sync._ensure_destination_dir", return_value=True),
        patch("compose_farm.plugins.builtin.sync._run_rsync", return_value=True),
    ):
        result = _handler(plugin)(context)

    assert result is not None
    assert result.success
    mock_progress.assert_not_called()
