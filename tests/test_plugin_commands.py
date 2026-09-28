"""Tests for the builtin commands plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from compose_farm.plugins import HookContext, PluginError, run_hook
from compose_farm.plugins.commands import CommandsPlugin
from tests.plugin_helpers import make_config, use_plugins

if TYPE_CHECKING:
    from pathlib import Path

    from compose_farm.config import Config


def _setup(tmp_path: Path, options: dict[str, Any]) -> Config:
    plugin = CommandsPlugin(options)
    plugin.name = "commands"
    return use_plugins(make_config(tmp_path, {"web": "h1"}), plugin)


class TestOptions:
    """Option validation."""

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({"nope": []}, "unknown option"),
            ({"before_up": "echo hi"}, "before_up must be a list"),
            ({"before_up": [{"run": "a", "local": "b"}]}, "exactly one of"),
            ({"before_up": [{"shell": "a"}]}, "exactly one of"),
            ({"before_up": [{"run": "echo {oops}"}]}, "unknown placeholder {oops}"),
            ({"before_up": [{"run": "echo {"}]}, "invalid template"),
            ({"before_up": [{"run": "echo {stack!r}"}]}, "cannot use conversions or format specs"),
            ({"before_up": [{"run": "echo {stack:>9}"}]}, "cannot use conversions or format specs"),
            ({"compose_args": "--env-file x"}, "compose_args must be a list of strings"),
        ],
    )
    def test_invalid_options(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            CommandsPlugin(options)


class TestHooks:
    """Steps run on the hook's host or locally; preflight; compose_args."""

    async def test_run_steps_execute_on_host_in_order(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        cfg = _setup(
            tmp_path,
            {
                "before_up": [
                    {"run": f"echo run {{stack}} {{host}} >> {log}"},
                    {"local": f"echo local {{stack}} >> {log}"},
                ]
            },
        )
        await run_hook(HookContext(cfg, "web", "h1"), "before_up")
        assert log.read_text().splitlines() == ["run web h1", "local web"]

    async def test_placeholders_are_shell_quoted(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        cfg = _setup(tmp_path, {"after_up": [{"run": f"printf %s {{source_host}} > {out}"}]})
        evil = f"a b; touch {tmp_path}/pwned"
        await run_hook(HookContext(cfg, "web", "h1", evil), "after_up")
        assert out.read_text() == evil
        assert not (tmp_path / "pwned").exists()

    async def test_failing_step_raises(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"after_source_stopped": [{"run": "exit 3"}]})
        with pytest.raises(PluginError, match=r"plugin commands\.after_source_stopped: .*exit 3"):
            await run_hook(HookContext(cfg, "web", "h2", "h1"), "after_source_stopped")

    async def test_preflight_reports_failing_checks(self, tmp_path: Path) -> None:
        cfg = _setup(
            tmp_path,
            {"preflight": [{"run": "test -d {stack_dir}"}, {"run": "test -e {stack_dir}/nope"}]},
        )
        plugin = cfg.get_plugins()[0]
        problems = await plugin.preflight(HookContext(cfg, "web", "h1"))
        assert len(problems) == 1
        assert "nope" in problems[0]
        assert "exit 1" in problems[0]

    async def test_preflight_includes_stderr(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"preflight": [{"run": "echo 'pool tank missing' >&2; exit 1"}]})
        [problem] = await cfg.get_plugins()[0].preflight(HookContext(cfg, "web", "h1"))
        assert problem.endswith("(exit 1): pool tank missing")

    def test_compose_args_are_rendered_verbatim(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"compose_args": ["--env-file", "/run/agenix/{stack}.env"]})
        assert cfg.compose_args("web", "h1") == ["--env-file", "/run/agenix/web.env"]


def test_registered_as_entry_point(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, {"web": "h1"})
    cfg.plugins = {"commands": {"after_up": [{"local": "true"}]}}
    [plugin] = cfg.get_plugins()
    assert isinstance(plugin, CommandsPlugin)
