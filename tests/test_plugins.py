"""Tests for the plugin API, loader, and dispatch helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from compose_farm.config import Config, load_config
from compose_farm.executor import CommandResult
from compose_farm.plugins import (
    HookContext,
    Plugin,
    PluginError,
    load_plugins,
    run_hook,
    run_hook_all,
    run_preflight,
)
from tests.plugin_helpers import Recorder, make_config, use_plugins

if TYPE_CHECKING:
    from pathlib import Path


class _EntryPoint:
    def __init__(self, name: str, obj: Any) -> None:
        self.name = name
        self._obj = obj
        self.loads = 0

    def load(self) -> Any:
        self.loads += 1
        return self._obj


def _entry_points(*eps: _EntryPoint) -> Any:
    return patch("importlib.metadata.entry_points", return_value=list(eps))


class _Strict(Plugin):
    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        if options:
            msg = "takes no options"
            raise PluginError(msg)


def _named(name: str, **options: Any) -> Recorder:
    plugin = Recorder(options)
    plugin.name = name
    return plugin


class TestLoader:
    """Loading plugins from entry points."""

    def test_no_plugins_skips_entry_point_scan(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        with patch("importlib.metadata.entry_points", side_effect=AssertionError):
            assert load_plugins(cfg) == ()
            assert cfg.get_plugins() == ()

    def test_loads_in_config_order_with_options(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        cfg.plugins = {"b": {"problems": ["x"]}, "a": None}
        with _entry_points(_EntryPoint("a", Recorder), _EntryPoint("b", Recorder)):
            plugins = load_plugins(cfg)
        assert [p.name for p in plugins] == ["b", "a"]
        assert plugins[0].options == {"problems": ["x"]}
        assert plugins[1].options == {}

    def test_unknown_plugin_lists_available(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        cfg.plugins = {"nope": None}
        with (
            _entry_points(_EntryPoint("known", Recorder)),
            pytest.raises(PluginError, match=r"Unknown plugin\(s\): nope \(available: known\)"),
        ):
            load_plugins(cfg)

    def test_option_error_names_plugin(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        cfg.plugins = {"strict": {"x": 1}}
        with (
            _entry_points(_EntryPoint("strict", _Strict)),
            pytest.raises(PluginError, match="plugin strict: takes no options"),
        ):
            load_plugins(cfg)

    def test_get_plugins_is_cached(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        cfg.plugins = {"rec": None}
        ep = _EntryPoint("rec", Recorder)
        with _entry_points(ep):
            first = cfg.get_plugins()
            second = cfg.get_plugins()
        assert first is second
        assert ep.loads == 1

    def test_load_config_validates_plugins(self, tmp_path: Path) -> None:
        path = tmp_path / "compose-farm.yaml"
        path.write_text(
            "compose_dir: /opt/compose\n"
            "hosts: {h1: localhost}\n"
            "stacks: {web: h1}\n"
            "plugins: {nope: null}\n"
        )
        with _entry_points(), pytest.raises(PluginError, match="Unknown plugin"):
            load_config(path)

    def test_non_plugin_class_is_rejected(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        cfg.plugins = {"bad": None}
        with (
            _entry_points(_EntryPoint("bad", dict)),
            pytest.raises(
                PluginError, match=r"plugin bad: dict is not a compose_farm\.plugins\.Plugin"
            ),
        ):
            load_plugins(cfg)

    def test_empty_plugins_section_is_allowed(self, tmp_path: Path) -> None:
        path = tmp_path / "compose-farm.yaml"
        path.write_text(
            "compose_dir: /opt/compose\nhosts: {h1: localhost}\nstacks: {web: h1}\nplugins:\n"
        )
        assert load_config(path).plugins == {}

    def test_plugins_must_be_a_mapping(self) -> None:
        with pytest.raises(ValidationError):
            Config.model_validate(
                {"hosts": {"h1": {"address": "localhost"}}, "stacks": {}, "plugins": ["sync"]}
            )


class TestDispatch:
    """Blocking, collecting, preflight, and compose_args dispatch."""

    async def test_run_hook_stops_at_first_failure(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(
            make_config(tmp_path, {"web": "h1"}),
            _named("a", events=events, fail=[("before_up", "web")]),
            _named("b", events=events),
        )
        with pytest.raises(PluginError, match=r"plugin a\.before_up: before_up failed"):
            await run_hook(HookContext(cfg, "web", "h1"), "before_up")
        assert events == [("before_up", "web", "h1", None)]

    async def test_run_hook_all_continues_after_failure(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(
            make_config(tmp_path, {"web": "h1"}),
            _named("a", events=events, fail=[("after_up", "web")]),
            _named("b", events=events),
        )
        errors = await run_hook_all(HookContext(cfg, "web", "h1"), "after_up")
        assert errors == ["plugin a.after_up: after_up failed"]
        assert len(events) == 2

    async def test_run_preflight_collects_problems_and_exceptions(self, tmp_path: Path) -> None:
        cfg = use_plugins(
            make_config(tmp_path, {"web": "h1"}),
            _named("a", problems=["pool missing"]),
            _named("b", fail=[("preflight", "web")]),
        )
        errors = await run_preflight(HookContext(cfg, "web", "h1"))
        assert errors == ["plugin a: pool missing", "plugin b: preflight failed"]

    def test_compose_args_concatenates_in_order(self, tmp_path: Path) -> None:
        cfg = use_plugins(
            make_config(tmp_path, {"web": "h1"}),
            _named("a", args=["--env-file", "/run/{stack}.env"]),
            _named("b", args=["--profile", "{host}"]),
        )
        assert cfg.compose_args("web", "h1") == [
            "--env-file",
            "/run/web.env",
            "--profile",
            "h1",
        ]

    def test_compose_args_empty_without_plugins(self, tmp_path: Path) -> None:
        assert make_config(tmp_path, {"web": "h1"}).compose_args("web", "h1") == []


class TestHookContextRun:
    """HookContext.run and run_local."""

    async def test_check_raises_with_command_host_and_exit(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        failed = CommandResult(stack="web", exit_code=3, success=False, stderr="nope\n")
        with (
            patch("compose_farm.plugins.run_command", AsyncMock(return_value=failed)),
            pytest.raises(PluginError, match=r"`false` failed on h1 \(exit 3\): nope"),
        ):
            await HookContext(cfg, "web", "h1").run("false")

    async def test_check_false_returns_result(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        failed = CommandResult(stack="web", exit_code=3, success=False)
        with patch("compose_farm.plugins.run_command", AsyncMock(return_value=failed)):
            result = await HookContext(cfg, "web", "h1").run("false", check=False)
        assert result is failed

    async def test_unknown_host_raises(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        with pytest.raises(PluginError, match="host 'gone' is not in config"):
            await HookContext(cfg, "web", "h1").run("true", host="gone")

    async def test_interrupt_propagates(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        interrupted = CommandResult(stack="web", exit_code=-2, success=False)
        with (
            patch("compose_farm.plugins.run_command", AsyncMock(return_value=interrupted)),
            pytest.raises(KeyboardInterrupt),
        ):
            await HookContext(cfg, "web", "h1").run("sleep 10")

    async def test_exit_255_is_a_failure_not_an_interrupt(self, tmp_path: Path) -> None:
        """Ssh and rsync exit 255 on connection errors; that must not look like Ctrl+C."""
        ctx = HookContext(make_config(tmp_path, {"web": "h1"}), "web", "h1")
        with pytest.raises(PluginError, match=r"exit 255"):
            await ctx.run_local("exit 255", stream=False)
        assert not (await ctx.run_local("exit 255", stream=False, check=False)).success

    async def test_run_executes_on_local_host(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        marker = tmp_path / "marker"
        await HookContext(cfg, "web", "h1").run(f"touch {marker}", stream=False)
        assert marker.exists()

    async def test_run_local_reports_exit_code(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        ctx = HookContext(cfg, "web", "h1")
        assert (await ctx.run_local("true", stream=False)).success
        with pytest.raises(PluginError, match=r"failed on local machine \(exit 4\)"):
            await ctx.run_local("exit 4", stream=False)
