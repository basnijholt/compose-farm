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
        if not isinstance(plugin, Plugin):
            msg = f"plugin {name}: {type(plugin).__name__} is not a compose_farm.plugins.Plugin"
            raise PluginError(msg)
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
