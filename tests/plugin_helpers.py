"""Shared helpers for plugin tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from compose_farm.config import Config, Host
from compose_farm.plugins import HookContext, Plugin

if TYPE_CHECKING:
    from types import ModuleType


class Recorder(Plugin):
    """Records hook calls into ``options["events"]``; fails ``(hook, stack)`` pairs in ``options["fail"]``."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Read events list, failures, preflight problems, and compose args from options."""
        super().__init__(options)
        self.events: list[Any] = options.get("events", [])
        self.fail: set[tuple[str, str]] = {(hook, stack) for hook, stack in options.get("fail", ())}
        self.problems: list[str] = list(options.get("problems", ()))
        self.args: list[str] = list(options.get("args", ()))

    def _record(self, hook: str, ctx: HookContext) -> None:
        self.events.append((hook, ctx.stack, ctx.host, ctx.source_host))
        if (hook, ctx.stack) in self.fail:
            msg = f"{hook} failed"
            raise RuntimeError(msg)

    async def preflight(self, ctx: HookContext) -> list[str]:
        self._record("preflight", ctx)
        return list(self.problems)

    async def before_up(self, ctx: HookContext) -> None:
        self._record("before_up", ctx)

    async def after_source_stopped(self, ctx: HookContext) -> None:
        self._record("after_source_stopped", ctx)

    async def after_up(self, ctx: HookContext) -> None:
        self._record("after_up", ctx)

    async def on_stack_removed(self, ctx: HookContext) -> None:
        self._record("on_stack_removed", ctx)

    def compose_args(self, ctx: HookContext) -> list[str]:
        return [arg.format(stack=ctx.stack, host=ctx.host) for arg in self.args]


def make_config(
    tmp_path: Path,
    stacks: dict[str, str | list[str]],
    hosts: tuple[str, ...] = ("h1", "h2"),
) -> Config:
    """Config with local hosts, a compose file per stack, and state in tmp_path."""
    compose_dir = tmp_path / "compose"
    for stack in stacks:
        (compose_dir / stack).mkdir(parents=True, exist_ok=True)
        (compose_dir / stack / "compose.yaml").write_text("services: {}\n")
    return Config(
        compose_dir=compose_dir,
        hosts={name: Host(address="localhost") for name in hosts},
        stacks=stacks,
        config_path=tmp_path / "compose-farm.yaml",
    )


def use_plugins(cfg: Config, *plugins: Plugin) -> Config:
    """Attach plugin instances to cfg without going through entry points."""
    for index, plugin in enumerate(plugins):
        plugin.name = plugin.name or f"p{index}"
    cfg.plugins = {plugin.name: None for plugin in plugins}
    cfg._loaded_plugins = plugins
    return cfg


def load_example_plugin(name: str) -> ModuleType:
    """Import ``examples/plugins/<name>/compose_farm_<name>.py`` without installing it."""
    path = Path(__file__).parent.parent / "examples" / "plugins" / name / f"compose_farm_{name}.py"
    module_name = f"compose_farm_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
