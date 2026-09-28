# Plugin System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use baspowers:subagent-driven-development (recommended) or baspowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an entry-point plugin system with six intent-level hooks plus two builtin plugins (`commands`, `sync`), so users can replace NFS and plaintext `.env` assumptions without forking.

**Architecture:** `compose_farm/plugins/__init__.py` holds the API (`Plugin`, `HookContext`, `PluginError`), the loader, and dispatch helpers. `Config` gains a `plugins:` mapping, caches loaded plugin instances, and exposes `compose_args(stack, host)` so the executor can add plugin arguments without importing the plugin package (which imports the executor). Lifecycle hooks are called from `operations.py` and the `up` CLI command only.

**Tech Stack:** Python 3.11, pydantic v2, asyncio, Typer, pytest (asyncio_mode=auto), ruff (select ALL), mypy, ty.

**Spec:** `docs/baspowers/specs/2026-09-28-plugin-system-design.md`

## Global Constraints

- Imports at module top level. The only allowed function-level import is `importlib.metadata.entry_points` in `load_plugins`, marked `# noqa: PLC0415` with a comment that it keeps CLI startup fast.
- `compose_farm.executor` must never import `compose_farm.plugins`.
- No cost when `plugins:` is empty: `load_plugins` returns `()` before touching entry points.
- Hooks run sequentially in config order; blocking hooks stop at the first failure, `run_hook_all` runs every plugin.
- `compose_args` values are paths/flags, never secrets (documented).
- Commit messages in normal English. Commits must be signed; if signing fails with "agent refused operation", run `export SSH_AUTH_SOCK=$(grep -o '/[^;]*' ~/.keychain/pc-sh | head -1)` first.
- Run `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run ty check src` before each commit (fix, then commit).

## File Structure

| File | Responsibility |
|------|----------------|
| `src/compose_farm/plugins/__init__.py` (new) | Plugin API, loader, dispatch helpers |
| `src/compose_farm/plugins/commands.py` (new) | Builtin `commands` plugin |
| `src/compose_farm/plugins/sync.py` (new) | Builtin `sync` plugin |
| `src/compose_farm/config.py` | `plugins` field, `get_plugins()`, `compose_args()`, validation in `load_config` |
| `src/compose_farm/executor.py` | `extra_args` in `_build_compose_command`/`_print_compose_command`, per-host args in every compose caller |
| `src/compose_farm/operations.py` | Hook calls in up/migrate/multi-host/orphans, rollback split + guard, plugin preflight, `up_stacks_direct` |
| `src/compose_farm/cli/lifecycle.py` | `up --service/--host` via `up_stacks_direct`, `apply` stray exclusion |
| `src/compose_farm/cli/management.py` | Plugin preflight errors in `cf check`, plugin list |
| `src/compose_farm/cli/config.py` | Plugin list in `cf config validate` |
| `pyproject.toml` | Entry points for builtins |
| `tests/plugin_helpers.py` (new) | `Recorder` plugin, `make_config`, `use_plugins` |
| `tests/test_plugins.py` (new) | API/loader/dispatch tests |
| `tests/test_plugin_lifecycle.py` (new) | Hook ordering in operations |
| `tests/test_plugin_commands.py` (new), `tests/test_plugin_sync.py` (new) | Builtins |
| `docs/plugins.md` (new), `docs/configuration.md`, `zensical.toml`, `compose-farm.example.yaml`, `CLAUDE.md` | Docs |

---

### Task 1: Plugin API, loader, and Config integration

**Files:**
- Create: `src/compose_farm/plugins/__init__.py`
- Create: `tests/plugin_helpers.py`
- Create: `tests/test_plugins.py`
- Modify: `src/compose_farm/config.py`

**Interfaces:**
- Produces:
  - `class PluginError(Exception)`
  - `@dataclass(frozen=True) class HookContext(cfg: Config, stack: str, host: str, source_host: str | None = None)` with `async run(command, *, host=None, stream=True, check=True) -> CommandResult` and `async run_local(command, *, stream=True, check=True) -> CommandResult`
  - `class Plugin` with `name: str`, `options`, hooks `preflight(ctx) -> list[str]`, `before_up(ctx)`, `after_source_stopped(ctx)`, `after_up(ctx)`, `on_stack_removed(ctx)`, `compose_args(ctx) -> list[str]`
  - `Hook = Literal["before_up", "after_source_stopped", "after_up", "on_stack_removed"]`
  - `load_plugins(cfg) -> tuple[Plugin, ...]`, `async run_hook(ctx, hook) -> None` (raises `PluginError("plugin <name>.<hook>: <err>")`), `async run_hook_all(ctx, hook) -> list[str]`, `async run_preflight(ctx) -> list[str]` (items `"plugin <name>: <problem>"`), `compose_args(cfg, stack, host) -> list[str]`
  - `Config.plugins: dict[str, dict[str, Any] | None]`, `Config.get_plugins() -> tuple[Plugin, ...]`, `Config.compose_args(stack, host) -> list[str]`
  - Test helpers: `Recorder`, `make_config(tmp_path, stacks, hosts=("h1", "h2"))`, `use_plugins(cfg, *plugins) -> Config`

- [ ] **Step 1: Write test helpers**

`tests/plugin_helpers.py`:

```python
"""Shared helpers for plugin tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from compose_farm.config import Config, Host
from compose_farm.plugins import HookContext, Plugin

if TYPE_CHECKING:
    from pathlib import Path


class Recorder(Plugin):
    """Records hook calls into ``options["events"]`` and fails ``(hook, stack)`` pairs in ``options["fail"]``."""

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        self.events: list[Any] = options.get("events", [])
        self.fail: set[tuple[str, str]] = set(options.get("fail", ()))
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
```

- [ ] **Step 2: Write failing tests**

`tests/test_plugins.py`:

```python
"""Tests for the plugin API, loader, and dispatch helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from compose_farm.config import Config, Host, load_config
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

    def test_plugins_must_be_a_mapping(self) -> None:
        with pytest.raises(ValidationError):
            Config(hosts={"h1": Host(address="localhost")}, stacks={}, plugins=["sync"])


class TestDispatch:
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_plugins.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'compose_farm.plugins'`.

- [ ] **Step 4: Implement the plugin package**

`src/compose_farm/plugins/__init__.py`:

```python
"""Plugin API: lifecycle hooks and extra docker compose arguments.

Plugins are enabled in the config's ``plugins:`` mapping (name -> options) and
discovered through the ``compose_farm.plugins`` entry-point group.
See docs/plugins.md for the hook contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from compose_farm.executor import _run_local_command, run_command

if TYPE_CHECKING:
    from compose_farm.config import Config
    from compose_farm.executor import CommandResult

ENTRY_POINT_GROUP = "compose_farm.plugins"

Hook = Literal["before_up", "after_source_stopped", "after_up", "on_stack_removed"]


class PluginError(Exception):
    """Raised by plugins to fail a hook with a user-facing message."""


@dataclass(frozen=True)
class HookContext:
    """The stack and host a hook is called for, plus helpers to run commands.

    ``source_host`` is the previous host during a migration. It is set even
    when that host is no longer in ``cfg.hosts``.
    """

    cfg: Config
    stack: str
    host: str
    source_host: str | None = None

    async def run(
        self,
        command: str,
        *,
        host: str | None = None,
        stream: bool = True,
        check: bool = True,
    ) -> CommandResult:
        """Run a shell command on ``host`` (default: this context's host)."""
        host_name = host or self.host
        if host_name not in self.cfg.hosts:
            msg = f"host {host_name!r} is not in config"
            raise PluginError(msg)
        label = f"{self.stack}@{host_name}"
        result = await run_command(
            self.cfg.hosts[host_name],
            command,
            self.stack,
            stream=stream,
            prefix=label,
            host_name=host_name,
            label=label,
        )
        return _checked(result, command, host_name, check=check)

    async def run_local(
        self,
        command: str,
        *,
        stream: bool = True,
        check: bool = True,
    ) -> CommandResult:
        """Run a shell command on the machine running cf."""
        result = await _run_local_command(
            command, self.stack, stream=stream, prefix=self.stack, label=self.stack
        )
        return _checked(result, command, "local machine", check=check)


def _checked(result: CommandResult, command: str, where: str, *, check: bool) -> CommandResult:
    """Turn Ctrl+C into KeyboardInterrupt and, with check, failures into PluginError."""
    if result.interrupted:
        raise KeyboardInterrupt
    if check and not result.success:
        detail = result.stderr.strip()
        msg = f"`{command}` failed on {where} (exit {result.exit_code})"
        raise PluginError(f"{msg}: {detail}" if detail else msg)
    return result


class Plugin:
    """Base class for plugins. Override any subset of the hooks.

    Hooks may run again for the same stack and host after a failure, so they
    must be idempotent. ``before_up`` and ``after_source_stopped`` must leave
    the source host's data intact; irreversible cleanup belongs in ``after_up``.
    """

    name = ""  # Set by the loader to the entry-point name

    def __init__(self, options: dict[str, Any]) -> None:
        """Store options. Subclasses validate them and raise PluginError."""
        self.options = options

    async def preflight(self, ctx: HookContext) -> list[str]:  # noqa: ARG002
        """Return problems that would stop the stack running on ctx.host. Must not mutate."""
        return []

    async def before_up(self, ctx: HookContext) -> None:
        """Prepare ctx.host before preflight and start. During a migration the source still runs."""

    async def after_source_stopped(self, ctx: HookContext) -> None:
        """Migration only: the source host is stopped and the target not started yet."""

    async def after_up(self, ctx: HookContext) -> None:
        """The stack started on ctx.host. After a migration, ctx.source_host is set."""

    async def on_stack_removed(self, ctx: HookContext) -> None:
        """An orphaned stack (removed from config) was stopped on ctx.host."""

    def compose_args(self, ctx: HookContext) -> list[str]:  # noqa: ARG002
        """Extra global docker compose arguments for ctx.stack on ctx.host. No I/O, no secrets."""
        return []


def load_plugins(cfg: Config) -> tuple[Plugin, ...]:
    """Instantiate the plugins enabled in ``cfg.plugins``, in config order."""
    if not cfg.plugins:
        return ()
    # Lazy import: entry-point scanning costs ~25ms and is only needed when plugins are enabled.
    from importlib.metadata import entry_points  # noqa: PLC0415

    available = {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}
    missing = [name for name in cfg.plugins if name not in available]
    if missing:
        names = ", ".join(sorted(available)) or "none"
        msg = f"Unknown plugin(s): {', '.join(missing)} (available: {names})"
        raise PluginError(msg)

    plugins: list[Plugin] = []
    for name, options in cfg.plugins.items():
        try:
            plugin = available[name].load()(options or {})
        except Exception as e:
            msg = f"plugin {name}: {e}"
            raise PluginError(msg) from e
        plugin.name = name
        plugins.append(plugin)
    return tuple(plugins)


async def run_hook(ctx: HookContext, hook: Hook) -> None:
    """Run a blocking hook on every plugin in order, stopping at the first failure."""
    for plugin in ctx.cfg.get_plugins():
        try:
            await getattr(plugin, hook)(ctx)
        except Exception as e:
            msg = f"plugin {plugin.name}.{hook}: {e}"
            raise PluginError(msg) from e


async def run_hook_all(ctx: HookContext, hook: Hook) -> list[str]:
    """Run a hook on every plugin, returning failure messages instead of raising."""
    errors: list[str] = []
    for plugin in ctx.cfg.get_plugins():
        try:
            await getattr(plugin, hook)(ctx)
        except Exception as e:
            errors.append(f"plugin {plugin.name}.{hook}: {e}")
    return errors


async def run_preflight(ctx: HookContext) -> list[str]:
    """Collect preflight problems from every plugin."""
    errors: list[str] = []
    for plugin in ctx.cfg.get_plugins():
        try:
            problems = await plugin.preflight(ctx)
        except Exception as e:
            problems = [str(e)]
        errors.extend(f"plugin {plugin.name}: {problem}" for problem in problems)
    return errors


def compose_args(cfg: Config, stack: str, host: str) -> list[str]:
    """Extra docker compose arguments from every plugin, in config order."""
    ctx = HookContext(cfg, stack, host)
    return [arg for plugin in cfg.get_plugins() for arg in plugin.compose_args(ctx)]
```

- [ ] **Step 5: Wire plugins into Config**

In `src/compose_farm/config.py`:

```python
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from .paths import config_search_paths, find_config_path
from .plugins import Plugin, load_plugins
from .plugins import compose_args as plugin_compose_args
```

Add to `Config` after `glances_stack`:

```python
    # Plugin name -> options (None for no options); mapping order is hook order
    plugins: dict[str, dict[str, Any] | None] = Field(default_factory=dict)
    config_path: Path = Path()  # Set by load_config()

    _loaded_plugins: tuple[Plugin, ...] | None = PrivateAttr(default=None)
```

Add methods to `Config` (after `get_stack_dir`):

```python
    def get_plugins(self) -> tuple[Plugin, ...]:
        """Enabled plugin instances, loaded once and cached."""
        if self._loaded_plugins is None:
            self._loaded_plugins = load_plugins(self)
        return self._loaded_plugins

    def compose_args(self, stack: str, host: str) -> list[str]:
        """Extra docker compose arguments contributed by plugins for a stack on a host."""
        return plugin_compose_args(self, stack, host)
```

In `load_config`, replace `return Config(**raw)` with:

```python
    config = Config(**raw)
    config.get_plugins()  # Fail early on unknown plugins or invalid plugin options
    return config
```

If ruff reports `TC001` for `Plugin` (only used in an annotation), keep the runtime import (pydantic resolves private-attribute annotations) and add `# noqa: TC001` with that reason.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_plugins.py tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 7: Lint, type check, full unit suite, commit**

Run: `uv run ruff check src tests && uv run ruff format src tests && uv run mypy src && uv run ty check src && uv run pytest -m "not browser" -n auto -q`
Expected: clean, all pass.

```bash
git add src/compose_farm/plugins/__init__.py src/compose_farm/config.py tests/plugin_helpers.py tests/test_plugins.py
git commit -m "feat(plugins): add plugin API, loader, and config integration"
```

---

### Task 2: Per-host compose arguments in the executor

**Files:**
- Modify: `src/compose_farm/executor.py` (`_print_compose_command`, `_build_compose_command`, `run_compose`, `run_compose_on_host`, `_run_sequential_stack_commands_on_host`, `_run_sequential_stack_commands_multi_host`, `check_stack_running`)
- Modify: `src/compose_farm/operations.py` (`_up_multi_host_stack`)
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `Config.compose_args(stack, host)`; test helpers `Recorder`, `make_config`, `use_plugins`.
- Produces: `_build_compose_command(stack_dir: Path, compose_cmd: str, extra_args: Sequence[str] = ()) -> str`; `_print_compose_command(host_name, stack, compose_cmd, extra_args: Sequence[str] = ())`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_executor.py`; add imports `from unittest.mock import AsyncMock, patch` if missing and `from tests.plugin_helpers import Recorder, make_config, use_plugins`)

```python
class TestPluginComposeArgs:
    """Plugin compose_args reach every compose command, computed per host."""

    @staticmethod
    def _env_plugin() -> Recorder:
        plugin = Recorder({"args": ["--env-file", "/run/{host} {stack}.env"]})
        plugin.name = "env"
        return plugin

    def test_build_compose_command_quotes_extra_args(self) -> None:
        cmd = _build_compose_command(Path("/opt/x"), "up -d", ["--env-file", "/run/a b.env"])
        assert cmd == "cd /opt/x && docker compose --env-file '/run/a b.env' up -d"

    def test_build_compose_command_without_extra_args_is_unchanged(self) -> None:
        assert _build_compose_command(Path("/opt/x"), "ps") == "cd /opt/x && docker compose ps"

    async def test_run_compose_and_on_host(self, tmp_path: Path) -> None:
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), self._env_plugin())
        ok = CommandResult(stack="web", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", AsyncMock(return_value=ok)) as mock:
            await run_compose(cfg, "web", "ps")
            await run_compose_on_host(cfg, "web", "h2", "down")
        assert "--env-file '/run/h1 web.env' ps" in mock.call_args_list[0].args[1]
        assert "--env-file '/run/h2 web.env' down" in mock.call_args_list[1].args[1]

    async def test_multi_host_commands_are_per_host(self, tmp_path: Path) -> None:
        cfg = use_plugins(make_config(tmp_path, {"glances": "all"}), self._env_plugin())
        ok = CommandResult(stack="glances", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", AsyncMock(return_value=ok)) as mock:
            await run_on_stacks(cfg, ["glances"], "pull")
            await run_on_stacks(cfg, ["glances"], "pull", filter_host="h2")
        commands = [call.args[1] for call in mock.call_args_list]
        assert "'/run/h1 glances.env' pull" in commands[0]
        assert "'/run/h2 glances.env' pull" in commands[1]
        assert "'/run/h2 glances.env' pull" in commands[2]

    async def test_check_stack_running_uses_args(self, tmp_path: Path) -> None:
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), self._env_plugin())
        ok = CommandResult(stack="web", exit_code=0, success=True, stdout="abc")
        with patch("compose_farm.executor.run_command", AsyncMock(return_value=ok)) as mock:
            assert await check_stack_running(cfg, "web", "h2")
        assert "'/run/h2 web.env' ps --status running -q" in mock.call_args.args[1]
```

Add to `tests/test_operations.py` (class `TestPreflightRequirements` neighbor):

```python
class TestMultiHostComposeArgs:
    async def test_up_multi_host_builds_command_per_host(self, tmp_path: Path) -> None:
        plugin = Recorder({"args": ["--env-file", "/run/{host}.env"]})
        plugin.name = "env"
        cfg = use_plugins(make_config(tmp_path, {"glances": ["h1", "h2"]}), plugin)
        ok = CommandResult(stack="glances", exit_code=0, success=True)
        with (
            patch(
                "compose_farm.operations.check_stack_requirements",
                AsyncMock(return_value=PreflightResult([], [], [], [])),
            ),
            patch("compose_farm.operations.run_command", AsyncMock(return_value=ok)) as mock,
            patch("compose_farm.operations.set_multi_host_stack"),
        ):
            await up_stacks(cfg, ["glances"])
        commands = [call.args[1] for call in mock.call_args_list]
        assert "--env-file /run/h1.env up -d" in commands[0]
        assert "--env-file /run/h2.env up -d" in commands[1]
```

(imports in `tests/test_operations.py`: `from tests.plugin_helpers import Recorder, make_config, use_plugins`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_executor.py::TestPluginComposeArgs tests/test_operations.py::TestMultiHostComposeArgs -q`
Expected: FAIL (`_build_compose_command() takes 2 positional arguments but 3 were given`, missing args in commands).

- [ ] **Step 3: Implement**

`executor.py` — add `Sequence` to the `TYPE_CHECKING` import from `collections.abc`, then:

```python
def _print_compose_command(
    host_name: str,
    stack: str,
    compose_cmd: str,
    extra_args: Sequence[str] = (),
) -> None:
    """Print the docker compose command being executed."""
    args = f"{shlex.join(extra_args)} " if extra_args else ""
    console.print(
        f"[dim][magenta]{host_name}[/magenta]: ({stack}) docker compose {args}{compose_cmd}[/dim]"
    )


def _build_compose_command(
    stack_dir: Path,
    compose_cmd: str,
    extra_args: Sequence[str] = (),
) -> str:
    """Build a compose command with a shell-safe working directory and extra global args."""
    args = f"{shlex.join(extra_args)} " if extra_args else ""
    return f"cd {shlex.quote(str(stack_dir))} && docker compose {args}{compose_cmd}"
```

`run_compose` body:

```python
    host_name = config.get_hosts(stack)[0]
    host = config.hosts[host_name]
    stack_dir = config.get_stack_dir(stack)
    extra_args = config.compose_args(stack, host_name)

    _print_compose_command(host_name, stack, compose_cmd, extra_args)

    # Use cd to let docker compose find the compose file on the remote host
    command = _build_compose_command(stack_dir, compose_cmd, extra_args)
```

`run_compose_on_host`: same pattern with its `host_name` argument.

`_run_sequential_stack_commands_on_host`: compute `extra_args = config.compose_args(stack, host_name)` before the loop; pass it to `_print_compose_command` and `_build_compose_command`.

`_run_sequential_stack_commands_multi_host`: delete the `command = _build_compose_command(stack_dir, cmd)` line above the host loop; inside the loop after `host = config.hosts[host_name]`:

```python
            extra_args = config.compose_args(stack, host_name)
            _print_compose_command(host_name, stack, cmd, extra_args)
            command = _build_compose_command(stack_dir, cmd, extra_args)
```

(remove the old `_print_compose_command(host_name, stack, cmd)` call in that loop).

`check_stack_running`:

```python
    command = _build_compose_command(
        stack_dir, "ps --status running -q", config.compose_args(stack, host_name)
    )
```

`operations._up_multi_host_stack`: replace the pre-loop `command = _build_compose_command(...)` with `up_cmd = build_up_cmd(pull=pull, build=build)` and inside the start loop, after `label = ...`:

```python
        command = _build_compose_command(stack_dir, up_cmd, cfg.compose_args(stack, host_name))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_executor.py tests/test_operations.py -q`
Expected: all pass.

- [ ] **Step 5: Lint, types, full unit suite, commit**

```bash
git add src/compose_farm/executor.py src/compose_farm/operations.py tests/test_executor.py tests/test_operations.py
git commit -m "feat(plugins): add plugin compose args to every compose command"
```

---

### Task 3: Plugin preflight in `up`, `cf check`, and host compatibility

**Files:**
- Modify: `src/compose_farm/operations.py` (`PreflightResult`, `check_stack_requirements`, `_report_preflight_failures`, `check_host_compatibility`)
- Modify: `src/compose_farm/cli/management.py` (`_check_stack_requirements`, `check`)
- Modify: `src/compose_farm/cli/config.py` (`config_validate`)
- Test: `tests/test_operations.py`, `tests/test_cli_management.py`

**Interfaces:**
- Consumes: `HookContext`, `run_preflight`.
- Produces: `PreflightResult.plugin_errors: tuple[str, ...] = ()` (5th field).

- [ ] **Step 1: Write failing tests** (append to `tests/test_operations.py`)

```python
class TestPluginPreflight:
    @staticmethod
    def _cfg(tmp_path: Path, **options: Any) -> Config:
        plugin = Recorder(options)
        plugin.name = "zfs"
        return use_plugins(make_config(tmp_path, {"web": "h1"}), plugin)

    async def test_plugin_problems_fail_preflight(self, tmp_path: Path) -> None:
        cfg = self._cfg(tmp_path, problems=["pool tank missing"])
        with patch(
            "compose_farm.operations.check_paths_exist",
            AsyncMock(side_effect=lambda _cfg, _host, paths: dict.fromkeys(paths, True)),
        ):
            result = await check_stack_requirements(cfg, "web", "h1")
        assert result.plugin_errors == ("plugin zfs: pool tank missing",)
        assert not result.ok

    async def test_plugin_exception_is_reported(self, tmp_path: Path) -> None:
        cfg = self._cfg(tmp_path, fail=[("preflight", "web")])
        with patch(
            "compose_farm.operations.check_paths_exist",
            AsyncMock(side_effect=lambda _cfg, _host, paths: dict.fromkeys(paths, True)),
        ):
            result = await check_stack_requirements(cfg, "web", "h1")
        assert result.plugin_errors == ("plugin zfs: preflight failed",)

    def test_report_includes_plugin_errors(self) -> None:
        preflight = PreflightResult([], [], [], [], ("plugin zfs: pool tank missing",))
        with patch("compose_farm.operations.print_error") as mock_print:
            _report_preflight_failures("web", "h1", preflight)
        printed = [call.args[0] for call in mock_print.call_args_list]
        assert "  plugin zfs: pool tank missing" in printed

    async def test_host_compatibility_counts_errors(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        preflight = PreflightResult([], [], [], ["ssh down"], ("plugin zfs: pool missing",))
        with patch(
            "compose_farm.operations.check_stack_requirements", AsyncMock(return_value=preflight)
        ):
            compat = await check_host_compatibility(cfg, "web")
        found, total, missing = compat["h1"]
        assert found < total
        assert missing == ["ssh down", "plugin zfs: pool missing"]
```

(imports: `from typing import Any`, `check_host_compatibility` from `compose_farm.operations`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_operations.py::TestPluginPreflight -q`
Expected: FAIL (`PreflightResult` takes 4 fields / no `plugin_errors`).

- [ ] **Step 3: Implement**

`operations.py` imports: `from .plugins import HookContext, PluginError, run_hook, run_hook_all, run_preflight` (later tasks use the rest; add now, ruff will flag unused until Task 4 — add only `HookContext, run_preflight` now).

```python
class PreflightResult(NamedTuple):
    """Result of pre-flight checks for a stack on a host."""

    missing_paths: list[str]
    missing_networks: list[str]
    missing_devices: list[str]
    check_errors: list[str]
    plugin_errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Return True if all checks passed."""
        return not (
            self.missing_paths
            or self.missing_networks
            or self.missing_devices
            or self.check_errors
            or self.plugin_errors
        )
```

End of `check_stack_requirements`:

```python
    plugin_errors = await run_preflight(HookContext(cfg, stack, host_name))
    return PreflightResult(
        missing_paths, missing_networks, missing_devices, check_errors, tuple(plugin_errors)
    )
```

`_report_preflight_failures`: after the devices loop add

```python
    for err in preflight.plugin_errors:
        print_error(f"  {err}")
```

`check_host_compatibility` loop body:

```python
        preflight = await check_stack_requirements(cfg, stack, host_name)
        missing = preflight.missing_paths + preflight.missing_networks + preflight.missing_devices
        errors = [*preflight.check_errors, *preflight.plugin_errors]
        results[host_name] = (total - len(missing), total + len(errors), missing + errors)
```

`cli/management.py` `_check_stack_requirements.check_stack`:

```python
            preflight_errors.extend(
                (stack, host_name, e) for e in [*preflight.check_errors, *preflight.plugin_errors]
            )
```

`check()` after `has_errors = _report_config_status(cfg)`:

```python
    if cfg.plugins:
        console.print(f"Plugins: {', '.join(cfg.plugins)}")
```

`cli/config.py` `config_validate` after the Stacks line:

```python
    if cfg.plugins:
        console.print(f"  Plugins: {', '.join(cfg.plugins)}")
```

- [ ] **Step 4: Add CLI test** (append to `tests/test_cli_management.py`, following its existing `check` test pattern of patching `load_config_or_exit`; use `--local` to skip SSH)

```python
def test_check_lists_plugins(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_config(tmp_path, {"web": "h1"})
    cfg.plugins = {"sync": None}
    cfg._loaded_plugins = ()
    with (
        patch("compose_farm.cli.management.load_config_or_exit", return_value=cfg),
        patch("compose_farm.cli.management._report_orphaned_stacks", return_value=False),
    ):
        check(stacks=None, local=True, config=None)
    assert "Plugins: sync" in capsys.readouterr().out
```

Adjust the patch target/import names to match how `tests/test_cli_management.py` already invokes `check` (read the file first); keep the assertion.

- [ ] **Step 5: Run tests, lint, full suite, commit**

Run: `uv run pytest tests/test_operations.py tests/test_cli_management.py -q`

```bash
git add src/compose_farm/operations.py src/compose_farm/cli/management.py src/compose_farm/cli/config.py tests/test_operations.py tests/test_cli_management.py
git commit -m "feat(plugins): report plugin preflight problems in up and cf check"
```

---

### Task 4: Lifecycle hooks in `up`, migration, and rollback

**Files:**
- Modify: `src/compose_farm/operations.py` (`_cleanup_and_rollback` split into `_cleanup_and_rollback` + `_rollback_to_source`, new `_run_before_up`, `_run_after_up`, `_up_stack_simple`, `_up_multi_host_stack`, `_migrate_stack`, `_up_single_stack`)
- Test: `tests/test_plugin_lifecycle.py` (new), `tests/test_operations.py` (update `_migrate_stack` call)

**Interfaces:**
- Consumes: `HookContext`, `PluginError`, `run_hook`, `run_hook_all`.
- Produces: `async _run_before_up(ctx: HookContext, *, label: str = "") -> CommandResult | None`; `async _run_after_up(ctx: HookContext) -> None`; `_migrate_stack(..., *, raw: bool = False, was_running: bool)`.

- [ ] **Step 1: Write failing tests** — `tests/test_plugin_lifecycle.py`:

```python
"""Plugin hook ordering in the up, migration, and rollback flows."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from compose_farm.executor import CommandResult
from compose_farm.operations import PreflightResult, up_stacks
from compose_farm.state import get_stack_host, set_stack_host
from tests.plugin_helpers import Recorder, make_config, use_plugins

if TYPE_CHECKING:
    from pathlib import Path

    from compose_farm.config import Config


class Harness(ExitStack):
    """Fakes compose execution in operations and records it alongside hook events."""

    def __init__(
        self,
        events: list[Any],
        *,
        failing: set[tuple[str, str]] | None = None,
        was_running: bool = True,
    ) -> None:
        super().__init__()
        self.events = events
        self.failing = failing or set()  # (compose command, host) pairs that fail
        self.was_running = was_running

    def _result(self, stack: str, command: str, host: str) -> CommandResult:
        self.events.append(("compose", stack, command, host))
        ok = (command, host) not in self.failing
        return CommandResult(stack=stack, exit_code=0 if ok else 1, success=ok, host=host)

    async def _preflight(self, cfg: Config, stack: str, host: str) -> PreflightResult:
        self.events.append(("core_preflight", stack, host))
        return PreflightResult([], [], [], [])

    async def _run_compose(self, cfg: Config, stack: str, command: str, **_: Any) -> CommandResult:
        return self._result(stack, command, cfg.get_hosts(stack)[0])

    async def _run_compose_on_host(
        self, cfg: Config, stack: str, host: str, command: str, **_: Any
    ) -> CommandResult:
        return self._result(stack, command, host)

    async def _run_compose_step(
        self, cfg: Config, stack: str, command: str, *, raw: bool, host: str | None = None
    ) -> CommandResult:
        return self._result(stack, command, host or cfg.get_hosts(stack)[0])

    async def _run_command(self, host: Any, command: str, stack: str, **kwargs: Any) -> CommandResult:
        return self._result(stack, command.split("docker compose ", 1)[1], kwargs["host_name"])

    def __enter__(self) -> Harness:
        super().__enter__()
        ops = "compose_farm.operations"
        self.enter_context(patch(f"{ops}.check_stack_requirements", side_effect=self._preflight))
        self.enter_context(patch(f"{ops}.run_compose", side_effect=self._run_compose))
        self.enter_context(patch(f"{ops}.run_compose_on_host", side_effect=self._run_compose_on_host))
        self.enter_context(patch(f"{ops}._run_compose_step", side_effect=self._run_compose_step))
        self.enter_context(patch(f"{ops}.run_command", side_effect=self._run_command))
        self.enter_context(
            patch(f"{ops}.check_stack_running", AsyncMock(return_value=self.was_running))
        )
        return self


def _recorder(events: list[Any], **options: Any) -> Recorder:
    plugin = Recorder({"events": events, **options})
    plugin.name = "rec"
    return plugin


class TestSimpleUp:
    async def test_hook_order(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert result.success
        assert events == [
            ("before_up", "web", "h1", None),
            ("core_preflight", "web", "h1"),
            ("compose", "web", "up -d", "h1"),
            ("after_up", "web", "h1", None),
        ]

    async def test_before_up_failure_only_fails_that_stack(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("before_up", "bad")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1", "bad": "h1"}), plugin)
        with Harness(events):
            results = {r.stack: r for r in await up_stacks(cfg, ["web", "bad"])}
        assert results["web"].success
        assert not results["bad"].success
        assert "plugin rec.before_up: before_up failed" in results["bad"].stderr
        assert ("compose", "bad", "up -d", "h1") not in events
        assert get_stack_host(cfg, "bad") is None

    async def test_after_up_failure_is_a_warning(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("after_up", "web")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), plugin)
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert result.success
        assert get_stack_host(cfg, "web") == "h1"


class TestMigration:
    async def test_hook_order(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "h1")
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert result.success
        assert events == [
            ("before_up", "web", "h2", "h1"),
            ("core_preflight", "web", "h2"),
            ("compose", "web", "pull --ignore-buildable", "h2"),
            ("compose", "web", "build", "h2"),
            ("compose", "web", "down", "h1"),
            ("after_source_stopped", "web", "h2", "h1"),
            ("compose", "web", "up -d", "h2"),
            ("after_up", "web", "h2", "h1"),
        ]
        assert get_stack_host(cfg, "web") == "h2"

    async def test_after_source_stopped_failure_rolls_back(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("after_source_stopped", "web")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), plugin)
        set_stack_host(cfg, "web", "h1")
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert not result.success
        assert events[-1] == ("compose", "web", "up -d", "h1")
        assert ("compose", "web", "up -d", "h2") not in events
        assert get_stack_host(cfg, "web") == "h1"

    async def test_no_rollback_when_source_was_not_running(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("after_source_stopped", "web")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), plugin)
        set_stack_host(cfg, "web", "h1")
        with Harness(events, was_running=False):
            await up_stacks(cfg, ["web"])
        assert ("compose", "web", "up -d", "h1") not in events

    async def test_failed_cleanup_skips_source_restart(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "h1")
        with Harness(events, failing={("up -d", "h2"), ("down", "h2")}):
            [result] = await up_stacks(cfg, ["web"])
        assert not result.success
        assert ("compose", "web", "down", "h2") in events
        assert ("compose", "web", "up -d", "h1") not in events

    async def test_failed_target_up_rolls_back(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "h1")
        with Harness(events, failing={("up -d", "h2")}):
            await up_stacks(cfg, ["web"])
        assert events[-2:] == [
            ("compose", "web", "down", "h2"),
            ("compose", "web", "up -d", "h1"),
        ]
        assert not any(e[0] == "after_up" for e in events)

    async def test_source_host_passed_when_not_in_config(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "gone")
        with Harness(events):
            await up_stacks(cfg, ["web"])
        assert events[0] == ("before_up", "web", "h2", "gone")
        assert not any(e[0] == "after_source_stopped" for e in events)
        assert ("compose", "web", "up -d", "h2") in events


class TestMultiHost:
    async def test_barrier_and_after_up_only_for_successes(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"glances": ["h1", "h2"]}), _recorder(events))
        with Harness(events, failing={("up -d", "h2")}):
            await up_stacks(cfg, ["glances"])
        assert events == [
            ("before_up", "glances", "h1", None),
            ("core_preflight", "glances", "h1"),
            ("before_up", "glances", "h2", None),
            ("core_preflight", "glances", "h2"),
            ("compose", "glances", "up -d", "h1"),
            ("compose", "glances", "up -d", "h2"),
            ("after_up", "glances", "h1", None),
        ]

    async def test_before_up_failure_starts_no_host(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("before_up", "glances")])
        cfg = use_plugins(make_config(tmp_path, {"glances": ["h1", "h2"]}), plugin)
        with Harness(events):
            [result] = await up_stacks(cfg, ["glances"])
        assert not result.success
        assert result.label == "glances@h1"
        assert not any(e[0] == "compose" for e in events)
```

In `tests/test_operations.py::TestMigrationCommands::test_migration_uses_pull_ignore_buildable`, add `was_running=True` to the `_migrate_stack(...)` call.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_plugin_lifecycle.py -q`
Expected: FAIL (no hook events recorded; rollback guard missing).

- [ ] **Step 3: Implement**

`operations.py` import: `from .plugins import HookContext, PluginError, run_hook, run_hook_all, run_preflight`.

Replace `_cleanup_and_rollback` with:

```python
async def _cleanup_and_rollback(
    cfg: Config,
    stack: str,
    target_host: str,
    current_host: str,
    prefix: str,
    *,
    was_running: bool,
    raw: bool = False,
) -> None:
    """Clean up failed start and attempt rollback to old host if it was running."""
    print_warning(f"{prefix} Cleaning up failed start on [magenta]{target_host}[/]")
    cleanup = await run_compose(cfg, stack, "down", raw=raw)
    if not cleanup.success:
        print_error(
            f"{prefix} Cleanup failed on [magenta]{target_host}[/]; not restarting on "
            f"[magenta]{current_host}[/] to avoid running on both hosts"
        )
        return
    await _rollback_to_source(cfg, stack, current_host, prefix, was_running=was_running, raw=raw)


async def _rollback_to_source(
    cfg: Config,
    stack: str,
    current_host: str,
    prefix: str,
    *,
    was_running: bool,
    raw: bool = False,
) -> None:
    """Restart the stack on its previous host if it was running there."""
    if not was_running:
        err_console.print(
            f"{prefix} [dim]Stack was not running on [magenta]{current_host}[/], skipping rollback[/]"
        )
        return

    print_warning(f"{prefix} Rolling back to [magenta]{current_host}[/]...")
    rollback_result = await run_compose_on_host(cfg, stack, current_host, "up -d", raw=raw)
    if rollback_result.success:
        print_success(f"{prefix} Rollback succeeded on [magenta]{current_host}[/]")
    else:
        print_error(f"{prefix} Rollback failed - stack is down")
```

Add after `_report_preflight_failures`:

```python
async def _run_before_up(ctx: HookContext, *, label: str = "") -> CommandResult | None:
    """Run before_up hooks; report and return a failed result if a plugin fails."""
    try:
        await run_hook(ctx, "before_up")
    except PluginError as e:
        print_error(f"{format_stack_prefix(ctx.stack)} {e}")
        return CommandResult(
            stack=ctx.stack, exit_code=1, success=False, stderr=str(e), host=ctx.host, label=label
        )
    return None


async def _run_after_up(ctx: HookContext) -> None:
    """Run after_up hooks; failures are warnings because the stack is already up."""
    errors = await run_hook_all(ctx, "after_up")
    for error in errors:
        print_warning(f"{format_stack_prefix(ctx.stack)} {error}")
    if errors and ctx.source_host:
        print_warning(
            f"{format_stack_prefix(ctx.stack)} Leftovers on "
            f"[magenta]{ctx.source_host}[/] may need manual cleanup"
        )
```

`_up_multi_host_stack` preflight loop:

```python
    for host_name in host_names:
        label = f"{stack}@{host_name}"
        if failure := await _run_before_up(HookContext(cfg, stack, host_name), label=label):
            results.append(failure)
            return results
        preflight = await check_stack_requirements(cfg, stack, host_name)
        if not preflight.ok:
            _report_preflight_failures(stack, host_name, preflight)
            results.append(
                CommandResult(stack=stack, exit_code=1, success=False, host=host_name, label=label)
            )
            return results
```

and the end:

```python
    if succeeded_hosts:
        set_multi_host_stack(cfg, stack, succeeded_hosts)
        for host_name in succeeded_hosts:
            await _run_after_up(HookContext(cfg, stack, host_name))
```

`_migrate_stack`: add keyword `was_running: bool` (required, after `raw`), and replace the tail:

```python
    # Stop on current host
    down_result = await _run_compose_step(cfg, stack, "down", raw=raw, host=current_host)
    if not down_result.success:
        return down_result

    try:
        await run_hook(HookContext(cfg, stack, target_host, current_host), "after_source_stopped")
    except PluginError as e:
        print_error(f"{prefix} {e}")
        await _rollback_to_source(cfg, stack, current_host, prefix, was_running=was_running, raw=raw)
        return CommandResult(stack=stack, exit_code=1, success=False, stderr=str(e), host=target_host)
    return None
```

Update its docstring: "Pre-pulls/builds images on target, stops the stack on the current host, then runs after_source_stopped hooks (rolling back on failure)."

`_up_single_stack` start:

```python
    target_host = cfg.get_hosts(stack)[0]
    current_host = get_stack_host(cfg, stack)
    source_host = current_host if current_host and current_host != target_host else None
    ctx = HookContext(cfg, stack, target_host, source_host)

    if failure := await _run_before_up(ctx):
        return failure
```

pass `was_running=was_running` to `_migrate_stack`, and on success:

```python
    if up_result.success:
        set_stack_host(cfg, stack, target_host)
        await _run_after_up(ctx)
```

`_up_stack_simple`:

```python
    target_host = cfg.get_hosts(stack)[0]
    ctx = HookContext(cfg, stack, target_host)

    if failure := await _run_before_up(ctx):
        return failure
    ...
    if result.success:
        set_stack_host(cfg, stack, target_host)
        await _run_after_up(ctx)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_plugin_lifecycle.py tests/test_operations.py -q`
Expected: all pass.

- [ ] **Step 5: Lint, types, full unit suite, commit**

```bash
git add src/compose_farm/operations.py tests/test_plugin_lifecycle.py tests/test_operations.py
git commit -m "feat(plugins): run lifecycle hooks during up and migration

Also refuse to restart the source host when cleaning up the failed target
fails, so a stack never runs on both hosts."
```

---

### Task 5: Hooks for `up --service` and `up --host`

**Files:**
- Modify: `src/compose_farm/operations.py` (new `up_stacks_direct`, `_direct_up_hosts`; import `run_on_stacks` from executor)
- Modify: `src/compose_farm/cli/lifecycle.py` (`up`)
- Test: `tests/test_plugin_lifecycle.py`, `tests/test_cli_lifecycle.py`

**Interfaces:**
- Consumes: `_run_before_up`, `_run_after_up`, `HookContext`.
- Produces: `async up_stacks_direct(cfg, stacks, compose_cmd, *, raw=False, filter_host=None) -> list[CommandResult]` — same call shape as `run_on_stacks`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_plugin_lifecycle.py`; import `up_stacks_direct`)

```python
def _fake_run_on_stacks(failing: set[str] | None = None) -> AsyncMock:
    async def run(cfg: Config, stacks: list[str], cmd: str, **kwargs: Any) -> list[CommandResult]:
        results = []
        for stack in stacks:
            hosts = [kwargs["filter_host"]] if kwargs.get("filter_host") else cfg.get_hosts(stack)
            for host in hosts:
                ok = stack not in (failing or set())
                results.append(CommandResult(stack=stack, exit_code=int(not ok), success=ok, host=host))
        return results

    return AsyncMock(side_effect=run)


class TestDirectUp:
    async def test_service_up_runs_hooks(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        fake = _fake_run_on_stacks()
        with patch("compose_farm.operations.run_on_stacks", fake):
            [result] = await up_stacks_direct(cfg, ["web"], "up -d app", raw=True)
        assert result.success
        fake.assert_awaited_once_with(cfg, ["web"], "up -d app", raw=True, filter_host=None)
        assert events == [("before_up", "web", "h1", None), ("after_up", "web", "h1", None)]

    async def test_before_up_failure_skips_stack(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("before_up", "bad")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1", "bad": "h1"}), plugin)
        fake = _fake_run_on_stacks()
        with patch("compose_farm.operations.run_on_stacks", fake):
            results = await up_stacks_direct(cfg, ["web", "bad"], "up -d", filter_host="h1")
        assert fake.await_args.args[1] == ["web"]
        assert {r.stack: r.success for r in results} == {"web": True, "bad": False}

    async def test_host_filter_limits_multi_host_hooks(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"glances": ["h1", "h2"]}), _recorder(events))
        with patch("compose_farm.operations.run_on_stacks", _fake_run_on_stacks()):
            await up_stacks_direct(cfg, ["glances"], "up -d", filter_host="h2")
        assert events == [
            ("before_up", "glances", "h2", None),
            ("after_up", "glances", "h2", None),
        ]

    async def test_no_after_up_for_failed_run(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        with patch("compose_farm.operations.run_on_stacks", _fake_run_on_stacks({"web"})):
            await up_stacks_direct(cfg, ["web"], "up -d")
        assert events == [("before_up", "web", "h1", None)]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_plugin_lifecycle.py::TestDirectUp -q`
Expected: `ImportError: cannot import name 'up_stacks_direct'`.

- [ ] **Step 3: Implement** (`operations.py`, after `up_stacks`; add `run_on_stacks` to the executor import list)

```python
def _direct_up_hosts(cfg: Config, stack: str, filter_host: str | None) -> list[str]:
    """Hosts that run_on_stacks touches for a stack (filter applies to multi-host stacks)."""
    if filter_host and cfg.is_multi_host(stack):
        return [filter_host]
    return cfg.get_hosts(stack)


async def up_stacks_direct(
    cfg: Config,
    stacks: list[str],
    compose_cmd: str,
    *,
    raw: bool = False,
    filter_host: str | None = None,
) -> list[CommandResult]:
    """Run an up command without migration or preflight, wrapped in before_up/after_up hooks.

    Used by `up --service` and `up --host`.
    """

    async def prepare(stack: str) -> CommandResult | None:
        for host_name in _direct_up_hosts(cfg, stack, filter_host):
            ctx = HookContext(cfg, stack, host_name)
            if failure := await _run_before_up(ctx, label=f"{stack}@{host_name}"):
                return failure
        return None

    prepared = await asyncio.gather(*(prepare(stack) for stack in stacks))
    failures = [failure for failure in prepared if failure is not None]
    ready = [stack for stack, failure in zip(stacks, prepared, strict=True) if failure is None]
    results = (
        await run_on_stacks(cfg, ready, compose_cmd, raw=raw, filter_host=filter_host)
        if ready
        else []
    )
    for result in results:
        if result.success and result.host:
            await _run_after_up(HookContext(cfg, result.stack, result.host))
    return [*failures, *results]
```

`cli/lifecycle.py` `up`: import `up_stacks_direct` from `compose_farm.operations`; in both the `service` and `host` branches replace `run_on_stacks(` with `up_stacks_direct(` (arguments unchanged). Update the two comments to say hooks still run.

- [ ] **Step 4: Update CLI tests**

In `tests/test_cli_lifecycle.py`, every test that calls `up(...)` or `update(...)` with `service=` set or `host=` set and patches `compose_farm.cli.lifecycle.run_on_stacks` must patch `compose_farm.cli.lifecycle.up_stacks_direct` instead (the call signature is identical, so the assertions stay). For parametrized tests that include both `up`/`update` and other commands (`stop`, `pull`, `restart`, ...), add a `patch_target` parameter: `"up_stacks_direct"` for `up`/`update`, `"run_on_stacks"` for the rest, and patch `f"compose_farm.cli.lifecycle.{patch_target}"`.

- [ ] **Step 5: Run tests, lint, full suite, commit**

Run: `uv run pytest tests/test_plugin_lifecycle.py tests/test_cli_lifecycle.py -q`

```bash
git add src/compose_farm/operations.py src/compose_farm/cli/lifecycle.py tests/test_plugin_lifecycle.py tests/test_cli_lifecycle.py
git commit -m "feat(plugins): run before_up/after_up for up --service and --host"
```

---

### Task 6: `on_stack_removed` for orphans and `apply` stray fix

**Files:**
- Modify: `src/compose_farm/operations.py` (`_stop_stacks_on_hosts`, `stop_orphaned_stacks`, new `_run_stack_removed`)
- Modify: `src/compose_farm/cli/lifecycle.py` (new `_exclude_migration_sources`, `apply`)
- Test: `tests/test_plugin_lifecycle.py`, `tests/test_cli_lifecycle.py`

**Interfaces:**
- Consumes: `HookContext`, `run_hook_all`, `get_stack_host`.
- Produces: `_stop_stacks_on_hosts(cfg, stacks_to_hosts, label="", *, removed=False)`; `_exclude_migration_sources(cfg, strays, migrations) -> dict[str, list[str]]`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_plugin_lifecycle.py` (imports `stop_orphaned_stacks`, `stop_stray_stacks`, `load_state`):

```python
def _fake_down() -> AsyncMock:
    async def down(cfg: Config, stack: str, host: str, command: str, **_: Any) -> CommandResult:
        return CommandResult(
            stack=stack, exit_code=0, success=True, host=host, label=f"{stack}@{host}"
        )

    return AsyncMock(side_effect=down)


class TestStackRemoved:
    async def test_orphan_fires_hook_and_leaves_state(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        set_stack_host(cfg, "old", "h1")
        with patch("compose_farm.operations.run_compose_on_host", _fake_down()):
            [result] = await stop_orphaned_stacks(cfg)
        assert result.success
        assert events == [("on_stack_removed", "old", "h1", None)]
        assert "old" not in load_state(cfg)

    async def test_hook_failure_keeps_state_for_retry(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("on_stack_removed", "old")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), plugin)
        set_stack_host(cfg, "old", "h1")
        with patch("compose_farm.operations.run_compose_on_host", _fake_down()):
            [result] = await stop_orphaned_stacks(cfg)
        assert not result.success
        assert "will retry" in result.stderr
        assert load_state(cfg)["old"] == "h1"

    async def test_strays_do_not_fire_hook(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        with patch("compose_farm.operations.run_compose_on_host", _fake_down()):
            await stop_stray_stacks(cfg, {"web": ["h2"]})
        assert events == []
```

Append to `tests/test_cli_lifecycle.py`:

```python
def test_apply_keeps_migration_source_running(tmp_path: Path) -> None:
    """A stack pending migration is not stopped as a stray on its state host."""
    cfg = make_config(tmp_path, {"web": "h2", "db": "h2"}, hosts=("h1", "h2", "h3"))
    set_stack_host(cfg, "web", "h1")
    strays = {"web": ["h1", "h3"], "db": ["h1"]}
    assert _exclude_migration_sources(cfg, strays, ["web"]) == {"web": ["h3"], "db": ["h1"]}
    assert _exclude_migration_sources(cfg, {"web": ["h1"]}, ["web"]) == {}
```

(imports: `from compose_farm.cli.lifecycle import _exclude_migration_sources`, `from compose_farm.state import set_stack_host`, `from tests.plugin_helpers import make_config`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_plugin_lifecycle.py::TestStackRemoved tests/test_cli_lifecycle.py::test_apply_keeps_migration_source_running -q`
Expected: FAIL (no events / import error).

- [ ] **Step 3: Implement**

`operations.py`, before `_stop_stacks_on_hosts`:

```python
async def _run_stack_removed(cfg: Config, result: CommandResult, host: str) -> CommandResult:
    """Run on_stack_removed hooks; a failure keeps the stack in state so it is retried."""
    errors = await run_hook_all(HookContext(cfg, result.stack, host), "on_stack_removed")
    if not errors:
        return result
    return CommandResult(
        stack=result.stack,
        exit_code=1,
        success=False,
        stderr=f"stopped, but {'; '.join(errors)} (will retry)",
        host=host,
        label=result.label,
    )
```

`_stop_stacks_on_hosts` signature gains `*, removed: bool = False` (document: "removed: Stacks were removed from config; run on_stack_removed hooks after each successful stop."). In the result loop:

```python
            result = await task
            if removed and result.success:
                result = await _run_stack_removed(cfg, result, host)
            results.append(result)
```

`stop_orphaned_stacks`: `results = await _stop_stacks_on_hosts(cfg, normalized, removed=True)`.

`cli/lifecycle.py`, after `_discover_strays` (import `get_stack_host` from `compose_farm.state` if not already imported):

```python
def _exclude_migration_sources(
    cfg: Config,
    strays: dict[str, list[str]],
    migrations: list[str],
) -> dict[str, list[str]]:
    """Drop migration sources from strays so the migration stops them itself.

    Stopping the source early would turn a live data transfer into downtime and
    disable rollback (the stack no longer counts as running on the source).
    """
    sources = {stack: get_stack_host(cfg, stack) for stack in migrations}
    filtered = {
        stack: [host for host in hosts if host != sources.get(stack)]
        for stack, hosts in strays.items()
    }
    return {stack: hosts for stack, hosts in filtered.items() if hosts}
```

In `apply`, change `strays = _discover_strays(cfg)` to
`strays = _exclude_migration_sources(cfg, _discover_strays(cfg), migrations)`.

- [ ] **Step 4: Run tests, lint, full suite, commit**

Run: `uv run pytest tests/test_plugin_lifecycle.py tests/test_cli_lifecycle.py -q`

```bash
git add src/compose_farm/operations.py src/compose_farm/cli/lifecycle.py tests/test_plugin_lifecycle.py tests/test_cli_lifecycle.py
git commit -m "feat(plugins): add on_stack_removed hook and keep migration sources running in apply

apply used to stop a stack on its old host as a stray before migrating it,
which also disabled rollback. The migration now stops the source itself."
```

---

### Task 7: Builtin `commands` plugin

**Files:**
- Create: `src/compose_farm/plugins/commands.py`
- Modify: `pyproject.toml` (entry points)
- Test: `tests/test_plugin_commands.py`

**Interfaces:**
- Consumes: `Plugin`, `PluginError`, `HookContext`.
- Produces: entry point `commands = "compose_farm.plugins.commands:CommandsPlugin"`.

- [ ] **Step 1: Write failing tests** — `tests/test_plugin_commands.py`:

```python
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
    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({"nope": []}, "unknown option"),
            ({"before_up": "echo hi"}, "before_up must be a list"),
            ({"before_up": [{"run": "a", "local": "b"}]}, "exactly one of"),
            ({"before_up": [{"shell": "a"}]}, "exactly one of"),
            ({"before_up": [{"run": "echo {oops}"}]}, "unknown placeholder {oops}"),
            ({"before_up": [{"run": "echo {"}]}, "invalid template"),
            ({"compose_args": "--env-file x"}, "compose_args must be a list of strings"),
        ],
    )
    def test_invalid_options(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            CommandsPlugin(options)


class TestHooks:
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

    def test_compose_args_are_rendered_verbatim(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"compose_args": ["--env-file", "/run/agenix/{stack}.env"]})
        assert cfg.compose_args("web", "h1") == ["--env-file", "/run/agenix/web.env"]


def test_registered_as_entry_point(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, {"web": "h1"})
    cfg.plugins = {"commands": {"after_up": [{"local": "true"}]}}
    [plugin] = cfg.get_plugins()
    assert isinstance(plugin, CommandsPlugin)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_plugin_commands.py -q`
Expected: `ModuleNotFoundError: No module named 'compose_farm.plugins.commands'`.

- [ ] **Step 3: Implement** — `src/compose_farm/plugins/commands.py`:

```python
"""Builtin ``commands`` plugin: shell commands from the config at lifecycle hooks.

Example::

    plugins:
      commands:
        before_up:
          - run: "mkdir -p /srv/data/{stack}"     # on the hook's host
          - local: "./scripts/notify.sh {stack}"  # on the machine running cf
        preflight:
          - run: "test -r /run/agenix/{stack}.env"
        compose_args: ["--env-file", "/run/agenix/{stack}.env"]
"""

from __future__ import annotations

import shlex
import string
from typing import TYPE_CHECKING, Any

from compose_farm.plugins import HookContext, Plugin, PluginError

if TYPE_CHECKING:
    from compose_farm.executor import CommandResult

_STEP_HOOKS = ("before_up", "after_source_stopped", "after_up", "on_stack_removed", "preflight")
_PLACEHOLDERS = ("stack", "host", "source_host", "compose_dir", "stack_dir")

Step = tuple[str, str]  # ("run" | "local", command template)


class CommandsPlugin(Plugin):
    """Run ``run:`` (on the hook's host) or ``local:`` commands per hook, and add compose args."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Validate hook steps, placeholders, and compose_args."""
        super().__init__(options)
        unknown = sorted(set(options) - {*_STEP_HOOKS, "compose_args"})
        if unknown:
            msg = f"unknown option(s): {', '.join(unknown)}"
            raise PluginError(msg)
        self.steps = {hook: _parse_steps(hook, options.get(hook, [])) for hook in _STEP_HOOKS}
        self.args = _parse_args(options.get("compose_args", []))

    async def before_up(self, ctx: HookContext) -> None:
        """Run the before_up steps."""
        await self._run_steps("before_up", ctx)

    async def after_source_stopped(self, ctx: HookContext) -> None:
        """Run the after_source_stopped steps."""
        await self._run_steps("after_source_stopped", ctx)

    async def after_up(self, ctx: HookContext) -> None:
        """Run the after_up steps."""
        await self._run_steps("after_up", ctx)

    async def on_stack_removed(self, ctx: HookContext) -> None:
        """Run the on_stack_removed steps."""
        await self._run_steps("on_stack_removed", ctx)

    async def preflight(self, ctx: HookContext) -> list[str]:
        """Report every preflight step that exits non-zero."""
        problems: list[str] = []
        for where, template in self.steps["preflight"]:
            command = _render(template, ctx, quote=True)
            result = await _execute(ctx, where, command, stream=False, check=False)
            if not result.success:
                problems.append(f"`{command}` failed (exit {result.exit_code})")
        return problems

    def compose_args(self, ctx: HookContext) -> list[str]:
        """Render compose_args; the core quotes each argument."""
        return [_render(arg, ctx, quote=False) for arg in self.args]

    async def _run_steps(self, hook: str, ctx: HookContext) -> None:
        for where, template in self.steps[hook]:
            await _execute(ctx, where, _render(template, ctx, quote=True))


async def _execute(
    ctx: HookContext, where: str, command: str, *, stream: bool = True, check: bool = True
) -> CommandResult:
    if where == "local":
        return await ctx.run_local(command, stream=stream, check=check)
    return await ctx.run(command, stream=stream, check=check)


def _parse_steps(hook: str, raw: object) -> list[Step]:
    if not isinstance(raw, list):
        msg = f"{hook} must be a list"
        raise PluginError(msg)
    steps: list[Step] = []
    for item in raw:
        if not (isinstance(item, dict) and len(item) == 1):
            msg = f"{hook}: each step needs exactly one of 'run' or 'local'"
            raise PluginError(msg)
        ((where, template),) = item.items()
        if where not in ("run", "local") or not isinstance(template, str):
            msg = f"{hook}: each step needs exactly one of 'run' or 'local' with a string command"
            raise PluginError(msg)
        _check_placeholders(template)
        steps.append((where, template))
    return steps


def _parse_args(raw: object) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(arg, str) for arg in raw):
        msg = "compose_args must be a list of strings"
        raise PluginError(msg)
    for arg in raw:
        _check_placeholders(arg)
    return list(raw)


def _check_placeholders(template: str) -> None:
    try:
        fields = [field for _, field, _, _ in string.Formatter().parse(template)]
    except ValueError as e:
        msg = f"invalid template {template!r}: {e}"
        raise PluginError(msg) from e
    for field in fields:
        if field is not None and field not in _PLACEHOLDERS:
            msg = (
                f"unknown placeholder {{{field}}} in {template!r} "
                f"(available: {', '.join(_PLACEHOLDERS)})"
            )
            raise PluginError(msg)


def _render(template: str, ctx: HookContext, *, quote: bool) -> str:
    values = {
        "stack": ctx.stack,
        "host": ctx.host,
        "source_host": ctx.source_host or "",
        "compose_dir": str(ctx.cfg.compose_dir),
        "stack_dir": str(ctx.cfg.get_stack_dir(ctx.stack)),
    }
    if quote:
        values = {key: shlex.quote(value) for key, value in values.items()}
    return template.format(**values)
```

`pyproject.toml`, after `[project.scripts]`:

```toml
[project.entry-points."compose_farm.plugins"]
commands = "compose_farm.plugins.commands:CommandsPlugin"
```

Then run `uv sync` so the entry point is registered in the dev environment.

- [ ] **Step 4: Run tests, lint, full suite, commit**

Run: `uv sync && uv run pytest tests/test_plugin_commands.py -q`

```bash
git add src/compose_farm/plugins/commands.py pyproject.toml tests/test_plugin_commands.py
git commit -m "feat(plugins): add builtin commands plugin"
```

---

### Task 8: Builtin `sync` plugin

**Files:**
- Create: `src/compose_farm/plugins/sync.py`
- Modify: `pyproject.toml`
- Test: `tests/test_plugin_sync.py`

**Interfaces:**
- Consumes: `Plugin`, `PluginError`, `HookContext`, `executor.build_ssh_command`, `executor.is_local`.
- Produces: entry point `sync = "compose_farm.plugins.sync:SyncPlugin"`; `rsync_argv(host, stack_dir, *, excludes, delete) -> list[str]`.

- [ ] **Step 1: Write failing tests** — `tests/test_plugin_sync.py`:

```python
"""Tests for the builtin sync plugin."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm.config import Host
from compose_farm.plugins import HookContext, PluginError
from compose_farm.plugins.sync import SyncPlugin, rsync_argv
from tests.plugin_helpers import make_config


class TestOptions:
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
    def test_uses_compose_farm_ssh_options(self) -> None:
        host = Host(address="10.0.0.5", user="bas", port=2222)
        argv = rsync_argv(host, Path("/opt/compose/web"), excludes=[".git"], delete=True)
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

    def test_without_delete(self) -> None:
        argv = rsync_argv(Host(address="h", user="u"), Path("/c/web"), excludes=[], delete=False)
        assert "--delete" not in argv


class TestBeforeUp:
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
        ):
            await SyncPlugin({"excludes": [".git"]}).before_up(HookContext(cfg, "web", "far"))
        run.assert_awaited_once_with(f"mkdir -p {shlex.quote(str(stack_dir))}", stream=False)
        command = run_local.await_args.args[0]
        assert command.startswith("rsync -az -e ")
        assert command.endswith(f"{stack_dir}/ bas@192.0.2.10:{stack_dir}/")

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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_plugin_sync.py -q`
Expected: `ModuleNotFoundError: No module named 'compose_farm.plugins.sync'`.

- [ ] **Step 3: Implement** — `src/compose_farm/plugins/sync.py`:

```python
"""Builtin ``sync`` plugin: rsync each stack directory to its host before starting it.

Replaces NFS for compose files. ``compose_dir`` must exist locally at the same
path it has on the hosts; ``compose_dir/<stack>/`` is copied to that path on
the target host before every start.

Example::

    plugins:
      sync:
        excludes: [".git"]
        delete: true
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from compose_farm.executor import build_ssh_command, is_local
from compose_farm.plugins import HookContext, Plugin, PluginError

if TYPE_CHECKING:
    from pathlib import Path

    from compose_farm.config import Host


class SyncPlugin(Plugin):
    """Copy the local stack directory to the target host before it starts."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Validate ``excludes`` (rsync patterns) and ``delete`` (default true)."""
        super().__init__(options)
        unknown = sorted(set(options) - {"excludes", "delete"})
        if unknown:
            msg = f"unknown option(s): {', '.join(unknown)}"
            raise PluginError(msg)
        excludes = options.get("excludes", [])
        if not isinstance(excludes, list) or not all(isinstance(p, str) for p in excludes):
            msg = "excludes must be a list of strings"
            raise PluginError(msg)
        delete = options.get("delete", True)
        if not isinstance(delete, bool):
            msg = "delete must be true or false"
            raise PluginError(msg)
        self.excludes: list[str] = excludes
        self.delete = delete

    async def before_up(self, ctx: HookContext) -> None:
        """Create the stack directory on the host and rsync the local copy into it."""
        host = ctx.cfg.hosts[ctx.host]
        if is_local(host):
            return
        stack_dir = ctx.cfg.get_stack_dir(ctx.stack)
        if not stack_dir.is_dir():
            msg = f"local stack directory not found: {stack_dir}"
            raise PluginError(msg)
        await ctx.run(f"mkdir -p {shlex.quote(str(stack_dir))}", stream=False)
        argv = rsync_argv(host, stack_dir, excludes=self.excludes, delete=self.delete)
        await ctx.run_local(shlex.join(argv), stream=False)


def rsync_argv(host: Host, stack_dir: Path, *, excludes: list[str], delete: bool) -> list[str]:
    """rsync command copying stack_dir to the same path on host over compose-farm's SSH."""
    ssh = build_ssh_command(host, "")[:-2]  # Drop "user@address" and the remote command
    argv = ["rsync", "-az", "-e", shlex.join(ssh)]
    if delete:
        argv.append("--delete")
    for pattern in excludes:
        argv.extend(["--exclude", pattern])
    argv.extend([f"{stack_dir}/", f"{host.user}@{host.address}:{stack_dir}/"])
    return argv
```

`pyproject.toml` entry points section:

```toml
[project.entry-points."compose_farm.plugins"]
commands = "compose_farm.plugins.commands:CommandsPlugin"
sync = "compose_farm.plugins.sync:SyncPlugin"
```

Run `uv sync`.

- [ ] **Step 4: Run tests, lint, full suite, commit**

Run: `uv sync && uv run pytest tests/test_plugin_sync.py -q`

```bash
git add src/compose_farm/plugins/sync.py pyproject.toml tests/test_plugin_sync.py
git commit -m "feat(plugins): add builtin sync plugin"
```

---

### Task 9: Documentation

**Files:**
- Create: `docs/plugins.md`
- Modify: `docs/configuration.md` (new `### plugins` section after `glances_stack`), `zensical.toml` (nav entry after Configuration), `compose-farm.example.yaml` (commented example), `CLAUDE.md` (architecture tree + one line under Key Design Decisions)

- [ ] **Step 1: Write `docs/plugins.md`** with these sections, using the exact YAML from the spec:
  1. **Overview** — what plugins are for (NFS replacement, secrets), enabled by `plugins:` mapping, order = hook order, unknown plugin fails config loading.
  2. **Builtin plugins** — `commands` (full example from spec, placeholder list, `run` vs `local`, preflight semantics, quoting rules) and `sync` (options, same-path requirement, skipped for local hosts, rsync required on the cf machine).
  3. **Hooks** — the spec's hook table (when / on failure), lifecycle order for migration (the 7 numbered steps), multi-host ordering, which commands are not hooked.
  4. **Writing a plugin** — minimal package example:
     ```python
     from compose_farm.plugins import HookContext, Plugin, PluginError

     class ZfsPlugin(Plugin):
         def __init__(self, options):
             super().__init__(options)
             self.parent = options.get("parent") or ""
             if not self.parent:
                 raise PluginError("parent is required")

         async def preflight(self, ctx: HookContext) -> list[str]:
             result = await ctx.run(f"zfs list {self.parent}", stream=False, check=False)
             return [] if result.success else [f"dataset {self.parent} not found"]

         async def before_up(self, ctx: HookContext) -> None:
             await ctx.run(f"zfs create -p {self.parent}/{ctx.stack}")
     ```
     ```toml
     [project.entry-points."compose_farm.plugins"]
     zfs = "my_package:ZfsPlugin"
     ```
     plus `HookContext` fields, `run`/`run_local` (`check`, `stream`), `PluginError`, the plugin contract (idempotent; source intact until `after_up`; refuse when `source_host not in ctx.cfg.hosts`).
  5. **Recipes** — agenix via `commands.compose_args` (+ the `.env` caveat), ZFS outline from the spec.
  6. **Security** — admonition: `compose_args` must contain paths, never secret values (printed, logged in the web UI, visible in `ps`).
  7. **Limitations** — local parsing does not see `compose_args`; `cf check` reports ZFS-managed paths as missing on hosts that never ran the stack.

- [ ] **Step 2: Update the other docs**
  - `docs/configuration.md`: `### plugins` with a 6-line example (`sync` + `commands.compose_args`) and a link to `plugins.md`.
  - `zensical.toml` nav: `{ "Plugins" = "plugins.md" },` after Configuration.
  - `compose-farm.example.yaml`: commented block after `traefik_stack`:
    ```yaml
    # Optional: plugins (name -> options), run in this order. See docs/plugins.md
    # plugins:
    #   sync:
    #     excludes: [".git"]
    #   commands:
    #     compose_args: ["--env-file", "/run/agenix/{stack}.env"]
    ```
  - `CLAUDE.md`: add `├── plugins/           # Plugin API, loader, hooks; builtin commands + sync plugins` to the tree and a Key Design Decisions item: "9. **Plugins**: entry-point plugins with intent-level hooks (`before_up`, `after_source_stopped`, `after_up`, `on_stack_removed`, `preflight`, `compose_args`); executor reaches them only via `Config.compose_args`".

- [ ] **Step 3: Verify docs build and commit**

Run: `uv run zensical build 2>&1 | tail -5` if zensical is available (otherwise skip and note it).

```bash
git add docs/plugins.md docs/configuration.md zensical.toml compose-farm.example.yaml CLAUDE.md
git commit -m "docs: document the plugin system"
```

---

### Task 10: Final verification

- [ ] **Step 1:** `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src tests && uv run ty check src`
- [ ] **Step 2:** `uv run pytest -m "not browser" -n auto -q` then `uv run pytest tests/test_cli_startup.py -q` (startup budget, not parallel)
- [ ] **Step 3:** Manual smoke test with a temp config using `commands` on a `localhost` host:
  ```bash
  tmp=$(mktemp -d); mkdir -p $tmp/compose/demo
  printf 'services:\n  hello:\n    image: hello-world\n' > $tmp/compose/demo/compose.yaml
  printf 'compose_dir: %s/compose\nhosts: {local: localhost}\nstacks: {demo: local}\nplugins:\n  commands:\n    before_up: [{run: "echo before {stack}@{host}"}]\n    after_up: [{local: "echo after {stack}"}]\n' $tmp > $tmp/compose-farm.yaml
  uv run cf check --local -c $tmp/compose-farm.yaml
  uv run cf up demo -c $tmp/compose-farm.yaml
  uv run cf down demo -c $tmp/compose-farm.yaml
  ```
  Expected: `Plugins: commands`, then `before demo@local` and `after demo` around the compose output.
