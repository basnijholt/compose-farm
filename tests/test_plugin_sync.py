"""Tests for the builtin sync plugin."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm.config import Host
from compose_farm.plugins import HookContext, PluginError
from compose_farm.plugins.sync import SyncPlugin, _rsync_argv
from tests.plugin_helpers import make_config


class TestOptions:
    """Option validation."""

    def test_delete_is_off_by_default(self) -> None:
        """Deleting host files missing locally could remove bind-mounted data."""
        assert SyncPlugin({}).delete is False

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({"nope": 1}, "unknown option"),
            ({"excludes": ".git"}, "excludes must be a list of strings"),
            ({"delete": "yes"}, "delete must be true or false"),
        ],
    )
    def test_invalid(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            SyncPlugin(options)


class TestRsyncArgv:
    """rsync command construction."""

    def test_uses_compose_farm_ssh_options(self) -> None:
        host = Host(address="10.0.0.5", user="bas", port=2222)
        argv = _rsync_argv(host, Path("/opt/compose/web"), excludes=[".git"], delete=True)
        assert argv[:3] == ["rsync", "-az", "-e"]
        ssh = shlex.split(argv[3])
        assert ssh[0] == "ssh"
        assert "StrictHostKeyChecking=yes" in ssh
        assert ssh[ssh.index("-p") + 1] == "2222"
        assert "bas@10.0.0.5" not in ssh
        assert argv[4:] == [
            "--delete",
            "--exclude",
            ".git",
            "/opt/compose/web/",
            "bas@10.0.0.5:/opt/compose/web/",
        ]

    def test_ipv6_destination_is_bracketed(self) -> None:
        argv = _rsync_argv(
            Host(address="fd00::5", user="u"), Path("/c/web"), excludes=[], delete=False
        )
        assert argv[-1] == "u@[fd00::5]:/c/web/"

    def test_without_delete(self) -> None:
        argv = _rsync_argv(Host(address="h", user="u"), Path("/c/web"), excludes=[], delete=False)
        assert "--delete" not in argv


class TestBeforeUp:
    """before_up behavior for local and remote hosts."""

    async def test_skips_local_host(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        with (
            patch.object(HookContext, "run", AsyncMock()) as run,
            patch.object(HookContext, "run_local", AsyncMock()) as run_local,
        ):
            await SyncPlugin({}).before_up(HookContext(cfg, "web", "h1"))
        run.assert_not_awaited()
        run_local.assert_not_awaited()

    async def test_remote_host_creates_dir_then_rsyncs(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "far"}, hosts=("far",))
        cfg.hosts["far"] = Host(address="192.0.2.10", user="bas")
        stack_dir = cfg.get_stack_dir("web")
        with (
            patch.object(HookContext, "run", AsyncMock()) as run,
            patch.object(HookContext, "run_local", AsyncMock()) as run_local,
            patch("compose_farm.plugins.sync.get_ssh_auth_sock", return_value=None),
        ):
            await SyncPlugin({"excludes": [".git"]}).before_up(HookContext(cfg, "web", "far"))
        run.assert_awaited_once_with(f"mkdir -p {shlex.quote(str(stack_dir))}", stream=False)
        assert run_local.await_args is not None
        command = run_local.await_args.args[0]
        assert command.startswith("rsync -az -e ")
        assert command.endswith(f"{stack_dir}/ bas@192.0.2.10:{stack_dir}/")

    async def test_uses_detected_ssh_agent(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "far"}, hosts=("far",))
        cfg.hosts["far"] = Host(address="192.0.2.10", user="bas")
        with (
            patch.object(HookContext, "run", AsyncMock()),
            patch.object(HookContext, "run_local", AsyncMock()) as run_local,
            patch("compose_farm.plugins.sync.get_ssh_auth_sock", return_value="/tmp/agent sock"),
        ):
            await SyncPlugin({}).before_up(HookContext(cfg, "web", "far"))
        assert run_local.await_args is not None
        assert run_local.await_args.args[0].startswith("SSH_AUTH_SOCK='/tmp/agent sock' rsync ")

    async def test_missing_local_dir_fails(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "far"}, hosts=("far",))
        cfg.hosts["far"] = Host(address="192.0.2.10", user="bas")
        cfg.stacks["ghost"] = "far"
        with pytest.raises(PluginError, match="local stack directory not found"):
            await SyncPlugin({}).before_up(HookContext(cfg, "ghost", "far"))


def test_registered_as_entry_point(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, {"web": "h1"})
    cfg.plugins = {"sync": None}
    [plugin] = cfg.get_plugins()
    assert isinstance(plugin, SyncPlugin)
