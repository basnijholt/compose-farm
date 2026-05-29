"""Built-in plugin to run local shell commands on lifecycle hook events."""

from __future__ import annotations

import subprocess
from typing import Any

from compose_farm.plugins.types import (
    HookContext,
    HookEvent,
    HookPolicy,
    HookRegistration,
    HookResult,
)

_PLUGIN_NAME = "command-hooks"


class CommandHooksPlugin:
    """Plugin that runs configured local commands for lifecycle events.

    Config shape (under plugin_config.command-hooks):

    plugin_config:
      command-hooks:
        hooks:
          pre_apply:
            - "echo preparing apply"
          pre_migrate:
            - "scripts/migrate-data.sh {stack} {source_host} {target_host}"

    Available placeholders:
    - {event}
    - {stack}
    - {source_host}
    - {target_host}
    - {dry_run}
    """

    def register_hooks(self) -> list[HookRegistration]:
        """Register one handler per event; handler no-ops if event has no commands."""
        return [
            HookRegistration(
                plugin=_PLUGIN_NAME,
                hook="run-commands",
                event=event,
                handler=self._handle_event,
                policy=HookPolicy.BLOCKING,
            )
            for event in HookEvent
        ]

    def _handle_event(self, context: HookContext) -> HookResult:
        """Run configured commands for a lifecycle event."""
        config = context.config.get_plugin_config(_PLUGIN_NAME)
        hooks = config.get("hooks", {})
        if not isinstance(hooks, dict):
            return HookResult(
                plugin=_PLUGIN_NAME,
                hook="run-commands",
                event=context.event,
                success=False,
                message="command-hooks config.hooks must be a mapping",
            )

        commands = hooks.get(context.event.value, [])
        if not commands:
            return HookResult(
                plugin=_PLUGIN_NAME,
                hook="run-commands",
                event=context.event,
                success=True,
                message="no commands configured",
            )

        if not isinstance(commands, list) or not all(isinstance(cmd, str) for cmd in commands):
            return HookResult(
                plugin=_PLUGIN_NAME,
                hook="run-commands",
                event=context.event,
                success=False,
                message=f"commands for event {context.event.value!r} must be a list[str]",
            )

        params = _event_params(context)
        rendered: list[str] = []
        for command in commands:
            try:
                rendered_command = command.format(**params)
            except KeyError as exc:
                return HookResult(
                    plugin=_PLUGIN_NAME,
                    hook="run-commands",
                    event=context.event,
                    success=False,
                    message=f"unknown placeholder in command: {exc}",
                )

            rendered.append(rendered_command)
            if context.dry_run:
                continue

            completed = subprocess.run(
                ["/bin/sh", "-lc", rendered_command],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.strip()
                return HookResult(
                    plugin=_PLUGIN_NAME,
                    hook="run-commands",
                    event=context.event,
                    success=False,
                    message=(
                        f"command failed ({completed.returncode}): {rendered_command}"
                        + (f"; stderr={stderr}" if stderr else "")
                    ),
                )

        message = "; ".join(rendered)
        if context.dry_run:
            message = f"dry-run: {message}"

        return HookResult(
            plugin=_PLUGIN_NAME,
            hook="run-commands",
            event=context.event,
            success=True,
            message=message,
        )


def _event_params(context: HookContext) -> dict[str, Any]:
    """Build format params for command template rendering."""
    return {
        "event": context.event.value,
        "stack": context.stack,
        "source_host": context.source_host or "",
        "target_host": context.target_host or "",
        "dry_run": str(context.dry_run).lower(),
    }
