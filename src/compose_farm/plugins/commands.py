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

_STEP_HOOKS = ("before_up", "after_source_stopped", "after_up", "after_stack_removed", "preflight")
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

    async def after_stack_removed(self, ctx: HookContext) -> None:
        """Run the after_stack_removed steps."""
        await self._run_steps("after_stack_removed", ctx)

    async def preflight(self, ctx: HookContext) -> list[str]:
        """Report every preflight step that exits non-zero."""
        problems: list[str] = []
        for where, template in self.steps["preflight"]:
            command = _render(template, ctx, quote=True)
            result = await _execute(ctx, where, command, stream=False, check=False)
            if not result.success:
                problem = f"`{command}` failed (exit {result.exit_code})"
                detail = result.stderr.strip()
                problems.append(f"{problem}: {detail}" if detail else problem)
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
        valid_where = isinstance(where, str) and where in ("run", "local")
        if not valid_where or not isinstance(template, str):
            msg = f"{hook}: each step needs exactly one of 'run' or 'local' with a string command"
            raise PluginError(msg)
        _check_placeholders(template)
        steps.append((where, template))
    return steps


def _parse_args(raw: object) -> list[str]:
    msg = "compose_args must be a list of strings"
    if not isinstance(raw, list):
        raise PluginError(msg)
    args: list[str] = []
    for arg in raw:
        if not isinstance(arg, str):
            raise PluginError(msg)
        _check_placeholders(arg)
        args.append(arg)
    return args


def _check_placeholders(template: str) -> None:
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as e:
        msg = f"invalid template {template!r}: {e}"
        raise PluginError(msg) from e
    for _, field, spec, conversion in parsed:
        if field is None:
            continue
        if field not in _PLACEHOLDERS:
            msg = (
                f"unknown placeholder {{{field}}} in {template!r} "
                f"(available: {', '.join(_PLACEHOLDERS)})"
            )
            raise PluginError(msg)
        if spec or conversion:
            # Values are shell-quoted before formatting; conversions would undo that
            msg = f"placeholder {{{field}}} in {template!r} cannot use conversions or format specs"
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
