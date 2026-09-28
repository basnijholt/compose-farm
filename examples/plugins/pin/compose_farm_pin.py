"""Example compose-farm plugin: keep stacks on the host they must run on.

Some stacks can't move: their host's IP is configured elsewhere (router port
forwards, Home Assistant, TV apps), or they need a USB device or GPU on that
host. Pinning them turns an accidental edit of ``stacks:`` into an error
instead of a migration::

    plugins:
      pin:
        gitea: nas                     # host only
        frigate:
          host: nas
          reason: static IP used in Home Assistant
        ollama:
          host: [pc, hp]               # any of these hosts
"""

from __future__ import annotations

from typing import Any

from compose_farm.plugins import HookContext, Plugin, PluginError


class PinPlugin(Plugin):
    """Refuse to start a pinned stack on any other host."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Parse ``stack: host`` or ``stack: {host, reason}`` entries."""
        super().__init__(options)
        self.pins: dict[str, tuple[list[str], str]] = {
            stack: _parse_pin(stack, spec) for stack, spec in options.items()
        }

    async def preflight(self, ctx: HookContext) -> list[str]:
        """Report a pinned stack configured on another host (e.g. in `cf check`)."""
        problem = self._problem(ctx)
        return [problem] if problem else []

    async def before_up(self, ctx: HookContext) -> None:
        """Stop the start before any other plugin (e.g. a data transfer) runs."""
        if problem := self._problem(ctx):
            raise PluginError(problem)

    def _problem(self, ctx: HookContext) -> str | None:
        if ctx.stack not in self.pins:
            return None
        hosts, reason = self.pins[ctx.stack]
        if ctx.host in hosts:
            return None
        why = f" ({reason})" if reason else ""
        return f"{ctx.stack} is pinned to {', '.join(hosts)}{why}; not starting it on {ctx.host}"


def _parse_pin(stack: str, spec: object) -> tuple[list[str], str]:
    reason = ""
    if isinstance(spec, dict):
        keys: dict[str, object] = {str(key): value for key, value in spec.items()}
        unknown = sorted(set(keys) - {"host", "reason"})
        if unknown:
            msg = f"{stack}: unknown key(s) {', '.join(unknown)} (use host, reason)"
            raise PluginError(msg)
        if "host" not in keys:
            msg = f"{stack}: host is required"
            raise PluginError(msg)
        raw_reason = keys.get("reason", "")
        if not isinstance(raw_reason, str):
            msg = f"{stack}: reason must be a string"
            raise PluginError(msg)
        spec, reason = keys["host"], raw_reason
    hosts = [spec] if isinstance(spec, str) else spec
    if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) for h in hosts):
        msg = f"{stack}: must be a host name, a list of host names, or {{host, reason}}"
        raise PluginError(msg)
    return [str(host) for host in hosts], reason
