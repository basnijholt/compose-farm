"""Plugin API: lifecycle hooks and extra docker compose arguments.

Plugins are enabled in the config's ``plugins:`` mapping (name -> options) and
discovered through the ``compose_farm.plugins`` entry-point group.
See docs/plugins.md for the hook contract.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, Literal

from compose_farm.console import err_console
from compose_farm.executor import _run_local_command, run_command

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint
    from pathlib import Path

    from compose_farm.config import Config
    from compose_farm.executor import CommandResult

ENTRY_POINT_GROUP = "compose_farm.plugins"

# Automatic install outcome per (config, plugin_packages): None or the failure.
# One attempt per process, because the web UI loads the config on every request.
_auto_install_results: dict[tuple[str, ...], str | None] = {}

Hook = Literal[
    "before_up", "after_source_stopped", "after_up", "after_stack_removed", "after_changes"
]


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
        return await _run_on_host(
            self.cfg, host or self.host, command, stack=self.stack, stream=stream, check=check
        )

    async def run_local(
        self,
        command: str,
        *,
        stream: bool = True,
        check: bool = True,
    ) -> CommandResult:
        """Run a shell command on the machine running cf."""
        return await _run_here(command, label=self.stack, stream=stream, check=check)


@dataclass(frozen=True)
class ChangesContext:
    """The stacks a cf up/update, down, or apply changed, plus helpers to run commands."""

    cfg: Config
    stacks: tuple[str, ...]

    async def run(
        self,
        command: str,
        *,
        host: str,
        stream: bool = True,
        check: bool = True,
    ) -> CommandResult:
        """Run a shell command on ``host``."""
        return await _run_on_host(self.cfg, host, command, stack="", stream=stream, check=check)

    async def run_local(
        self,
        command: str,
        *,
        stream: bool = True,
        check: bool = True,
    ) -> CommandResult:
        """Run a shell command on the machine running cf."""
        return await _run_here(command, label="local", stream=stream, check=check)


async def _run_on_host(
    cfg: Config, host_name: str, command: str, *, stack: str, stream: bool, check: bool
) -> CommandResult:
    if host_name not in cfg.hosts:
        msg = f"host {host_name!r} is not in config"
        raise PluginError(msg)
    label = f"{stack}@{host_name}" if stack else host_name
    result = await run_command(
        cfg.hosts[host_name],
        command,
        stack,
        stream=stream,
        prefix=label,
        host_name=host_name,
        label=label,
    )
    return _checked(result, command, host_name, check=check)


async def _run_here(command: str, *, label: str, stream: bool, check: bool) -> CommandResult:
    result = await _run_local_command(command, label, stream=stream, prefix=label, label=label)
    return _checked(result, command, "local machine", check=check)


def _checked(result: CommandResult, command: str, where: str, *, check: bool) -> CommandResult:
    """Turn Ctrl+C into KeyboardInterrupt and, with check, failures into PluginError."""
    # Only signal deaths count as Ctrl+C: ssh and rsync exit 255 on connection errors,
    # which must fail the hook (and trigger rollback) instead of aborting the run.
    if result.exit_code < 0:
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

    async def after_stack_removed(self, ctx: HookContext) -> None:
        """An orphaned stack (removed from config) was stopped on ctx.host."""

    async def after_changes(self, ctx: ChangesContext) -> None:
        """Once after a cf up/update, down, or apply that changed stacks (ctx.stacks)."""

    def compose_args(self, ctx: HookContext) -> list[str]:  # noqa: ARG002
        """Extra global docker compose arguments for ctx.stack on ctx.host. No I/O, no secrets."""
        return []


def load_plugins(cfg: Config) -> tuple[Plugin, ...]:
    """Instantiate the plugins enabled in ``cfg.plugins``, in config order.

    Missing plugins are installed from ``cfg.plugin_packages`` first when
    ``cfg.plugin_auto_install`` is on.
    """
    if not cfg.plugins:
        return ()
    available = _available_plugins()
    missing = [name for name in cfg.plugins if name not in available]
    hint = "List their packages under plugin_packages and run `cf plugins install`."
    if missing and cfg.plugin_auto_install and cfg.plugin_packages:
        if failure := _auto_install(cfg, missing):
            hint = f"Installing plugin_packages failed: {failure}. "
            hint += "Run `cf plugins install` to see why."
        else:
            available = _available_plugins()
            missing = [name for name in cfg.plugins if name not in available]
            hint = "The installed plugin_packages don't provide them."
    if missing:
        names = ", ".join(sorted(available)) or "none"
        msg = f"Unknown plugin(s): {', '.join(missing)} (available: {names}). {hint}"
        raise PluginError(msg)

    plugins: list[Plugin] = []
    for name, options in cfg.plugins.items():
        try:
            plugin = available[name].load()(options or {})
        except Exception as e:
            msg = f"plugin {name}: {e}"
            raise PluginError(msg) from e
        if not isinstance(plugin, Plugin):
            msg = f"plugin {name}: {type(plugin).__name__} is not a compose_farm.plugins.Plugin"
            raise PluginError(msg)
        plugin.name = name
        plugins.append(plugin)
    return tuple(plugins)


def _available_plugins() -> dict[str, EntryPoint]:
    # Lazy import: entry-point scanning costs ~25ms and is only needed when plugins are enabled.
    from importlib.metadata import entry_points  # noqa: PLC0415

    return {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}


def _auto_install(cfg: Config, missing: list[str]) -> str | None:
    """Install plugin_packages once; return why it failed, if it did."""
    key = (str(cfg.config_path), *cfg.plugin_packages)
    if key not in _auto_install_results:
        # Names the plugins, not the packages: package URLs can hold credentials
        err_console.print(f"[dim]Installing plugin_packages for {', '.join(missing)}...[/]")
        try:
            # Installer output goes to stderr (fd 2), so scripted stdout stays clean
            install_packages(cfg.plugin_packages, cfg.config_path.parent, stdout=2)
        except PluginError as e:
            _auto_install_results[key] = str(e)
        else:
            _auto_install_results[key] = None
    return _auto_install_results[key]


def install_packages(packages: list[str], cwd: Path, *, stdout: int | None = None) -> None:
    """Install plugin packages into the Python environment running compose-farm.

    Uses uv when it is on PATH, else pip, and runs in ``cwd`` so relative local
    paths resolve against it.
    """
    command = [*_installer(), "--", *(_requirement(package) for package in packages)]
    try:
        returncode = subprocess.run(command, check=False, cwd=cwd, stdout=stdout).returncode
    except OSError as e:
        msg = f"could not run the installer: {e}"
        raise PluginError(msg) from e
    if returncode != 0:
        msg = f"the installer exited with status {returncode}"
        raise PluginError(msg)
    importlib.invalidate_caches()  # Let the entry-point scan see the new packages


def _installer() -> list[str]:
    if uv := shutil.which("uv"):
        return [uv, "pip", "install", "--python", sys.executable]
    if find_spec("pip") is None:  # uv tool environments have no pip
        msg = "it needs uv on PATH or pip in compose-farm's Python"
        raise PluginError(msg)
    return [sys.executable, "-m", "pip", "install"]


def _requirement(package: str) -> str:
    """Expand ``github:OWNER/REPO[/SUBDIR][@REF]`` into a pip git URL; pass others through."""
    if not package.startswith("github:"):
        return package
    path, _, ref = package.removeprefix("github:").partition("@")
    owner, _, rest = path.partition("/")
    repo, _, subdir = rest.partition("/")
    if not owner or not repo:
        msg = f"invalid plugin package {package!r}: use github:OWNER/REPO/SUBDIR@REF (SUBDIR and REF are optional)"
        raise PluginError(msg)
    url = f"git+https://github.com/{owner}/{repo}" + (f"@{ref}" if ref else "")
    return f"{url}#subdirectory={subdir}" if subdir else url


async def run_hook(ctx: HookContext, hook: Hook) -> None:
    """Run a blocking hook on every plugin in order, stopping at the first failure."""
    for plugin in ctx.cfg.get_plugins():
        try:
            await getattr(plugin, hook)(ctx)
        except Exception as e:
            msg = f"plugin {plugin.name}.{hook}: {e}"
            raise PluginError(msg) from e


async def run_hook_all(ctx: HookContext | ChangesContext, hook: Hook) -> list[str]:
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
    args: list[str] = []
    for plugin in cfg.get_plugins():
        try:
            args.extend(plugin.compose_args(ctx))
        except Exception as e:
            msg = f"plugin {plugin.name}.compose_args: {e}"
            raise PluginError(msg) from e
    return args
