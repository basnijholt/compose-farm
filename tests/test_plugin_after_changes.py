"""Tests for the after_changes hook: once per cf command that changed deployments."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from compose_farm.cli.common import maybe_run_after_changes
from compose_farm.cli.lifecycle import apply, down, up
from compose_farm.executor import CommandResult
from compose_farm.plugins import ChangesContext, PluginError, run_hook_all
from tests.plugin_helpers import Recorder, make_config, use_plugins

if TYPE_CHECKING:
    from pathlib import Path

    from compose_farm.config import Config


def _result(stack: str, *, ok: bool = True, host: str = "h1") -> CommandResult:
    return CommandResult(stack=stack, exit_code=0 if ok else 1, success=ok, host=host)


def _cfg(tmp_path: Path, events: list[Any], **options: Any) -> Config:
    plugin = Recorder({"events": events, **options})
    plugin.name = "rec"
    return use_plugins(make_config(tmp_path, {"web": "h1", "db": "h1", "app": "h2"}), plugin)


class TestChangesContext:
    """Command helpers without a per-stack host."""

    async def test_run_needs_a_known_host(self, tmp_path: Path) -> None:
        ctx = ChangesContext(make_config(tmp_path, {"web": "h1"}), ("web",))
        with pytest.raises(PluginError, match="host 'gone' is not in config"):
            await ctx.run("true", host="gone")

    async def test_run_and_run_local(self, tmp_path: Path) -> None:
        ctx = ChangesContext(make_config(tmp_path, {"web": "h1"}), ("web",))
        marker = tmp_path / "marker"
        await ctx.run(f"touch {marker}", host="h1", stream=False)
        assert marker.exists()
        with pytest.raises(PluginError, match=r"failed on local machine \(exit 3\)"):
            await ctx.run_local("exit 3", stream=False)

    async def test_dispatch_runs_every_plugin(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = _cfg(tmp_path, events, fail=[("after_changes", "*")])
        errors = await run_hook_all(ChangesContext(cfg, ("web",)), "after_changes")
        assert errors == ["plugin rec.after_changes: after_changes failed"]
        assert events == [("after_changes", ("web",))]


class TestMaybeRunAfterChanges:
    """The CLI calls the hook once with the stacks that succeeded."""

    def test_once_with_successful_stacks(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = _cfg(tmp_path, events)
        results = [
            _result("web"),
            _result("app", host="h2"),
            _result("web", host="h2"),
            _result("db", ok=False),
        ]
        maybe_run_after_changes(cfg, results)
        assert events == [("after_changes", ("app", "web"))]

    def test_skipped_when_nothing_succeeded(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = _cfg(tmp_path, events)
        maybe_run_after_changes(cfg, [_result("db", ok=False)])
        maybe_run_after_changes(cfg, [])
        assert events == []

    def test_failures_are_warnings(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = _cfg(tmp_path, [], fail=[("after_changes", "*")])
        maybe_run_after_changes(cfg, [_result("web")])
        assert "plugin rec.after_changes: after_changes failed" in capsys.readouterr().err


class TestWiring:
    """Commands that change deployments call the hook exactly once."""

    @staticmethod
    def _run_async_returns(results: list[CommandResult]) -> Any:
        def run(coro: Any) -> list[CommandResult]:
            coro.close()
            return results

        return run

    def test_up(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        results = [_result("web")]
        with (
            patch("compose_farm.cli.common.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.cli.lifecycle.run_async", side_effect=self._run_async_returns(results)
            ),
            patch("compose_farm.cli.lifecycle.maybe_regenerate_traefik"),
            patch("compose_farm.cli.lifecycle.report_results"),
            patch("compose_farm.cli.lifecycle.maybe_run_after_changes") as hook,
        ):
            up(stacks=["web"], all_stacks=False, host=None, service=None, config=None)
        hook.assert_called_once_with(cfg, results)

    def test_down_orphaned(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        results = [_result("old")]
        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={"old": "h1"}),
            patch(
                "compose_farm.cli.lifecycle.run_async", side_effect=self._run_async_returns(results)
            ),
            patch("compose_farm.cli.lifecycle.report_results"),
            patch("compose_farm.cli.lifecycle.maybe_run_after_changes") as hook,
        ):
            down(stacks=None, all_stacks=False, orphaned=True, host=None, config=None)
        hook.assert_called_once_with(cfg, results)

    def test_apply_calls_once_with_all_results(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1", "db": "h1"})
        orphan, started = [_result("old")], [_result("db")]
        with (
            patch("compose_farm.cli.lifecycle.load_config_or_exit", return_value=cfg),
            patch("compose_farm.cli.lifecycle.get_orphaned_stacks", return_value={"old": "h1"}),
            patch("compose_farm.cli.lifecycle.get_stacks_needing_migration", return_value=[]),
            patch("compose_farm.cli.lifecycle.get_stacks_not_in_state", return_value=["db"]),
            patch("compose_farm.cli.lifecycle._discover_strays", return_value={}),
            patch("compose_farm.cli.lifecycle.run_async", side_effect=[orphan, started]),
            patch("compose_farm.cli.lifecycle.stop_orphaned_stacks"),
            patch("compose_farm.cli.lifecycle.up_stacks"),
            patch("compose_farm.cli.lifecycle.maybe_regenerate_traefik"),
            patch("compose_farm.cli.lifecycle.report_results"),
            patch("compose_farm.cli.lifecycle.maybe_run_after_changes") as hook,
        ):
            apply(dry_run=False, no_orphans=False, no_strays=True, full=False, config=None)
        hook.assert_called_once_with(cfg, orphan + started)
