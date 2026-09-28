"""Example compose-farm plugin: enforce Traefik routing conventions during preflight.

Checks each stack's Traefik router labels before it starts (and in `cf check`),
so a router that breaks your conventions never goes live::

    plugins:
      traefik-policy:
        entrypoints_require:
          wan: [websecure]   # a router on "wan" must also be on "websecure"
"""

from __future__ import annotations

from typing import Any

from compose_farm.plugins import HookContext, Plugin, PluginError
from compose_farm.traefik import generate_traefik_config


class TraefikPolicyPlugin(Plugin):
    """Report routers that are on an entrypoint without the entrypoints it requires."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Validate ``entrypoints_require`` (entrypoint -> entrypoints it requires)."""
        super().__init__(options)
        unknown = sorted(set(options) - {"entrypoints_require"})
        if unknown:
            msg = f"unknown option(s): {', '.join(unknown)}"
            raise PluginError(msg)
        if "entrypoints_require" not in options:
            msg = "entrypoints_require is required"
            raise PluginError(msg)
        rules = options["entrypoints_require"]
        if not isinstance(rules, dict):
            msg = "entrypoints_require must map an entrypoint to the entrypoints it requires"
            raise PluginError(msg)
        self.rules = {
            str(ep): _names(f"entrypoints_require.{ep}", req) for ep, req in rules.items()
        }

    async def preflight(self, ctx: HookContext) -> list[str]:
        """Check the stack's routers against the rules."""
        try:
            dynamic, _ = generate_traefik_config(ctx.cfg, [ctx.stack], check_all=True)
        except (FileNotFoundError, ValueError):
            return []  # compose-farm's own checks report broken compose files
        problems = []
        for name, router in dynamic.get("http", {}).get("routers", {}).items():
            entrypoints = router.get("entrypoints", [])
            if isinstance(entrypoints, str):
                entrypoints = [entrypoints]
            for entrypoint, required in self.rules.items():
                missing = [ep for ep in required if ep not in entrypoints]
                if entrypoint in entrypoints and missing:
                    problems.append(
                        f"router {name} is on entrypoint {entrypoint} but not on {', '.join(missing)}"
                    )
        return problems


def _names(where: str, value: object) -> list[str]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        msg = f"{where} must be an entrypoint name or a list of them"
        raise PluginError(msg)
    return [str(item) for item in items]
