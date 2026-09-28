"""Example compose-farm plugin: secrets decrypted by agenix instead of plaintext .env files.

agenix (or sops-nix) decrypts secrets on every host into ``/run/agenix/``. This
plugin hands them to ``docker compose`` as extra ``--env-file`` arguments and
checks during preflight that they exist on the target host::

    plugins:
      agenix:
        secrets_dir: /run/agenix         # default
        stacks:
          mealie: mealie.env             # one env file
          forgejo: [forgejo.env, db.env]  # several env files
          vaultwarden:
            env: vaultwarden.env
            files: [admin-token]         # only checked; use it via compose `secrets:`

Relative names are resolved in ``secrets_dir``; absolute paths are used as-is.
Stacks that are not listed are left alone.
"""

from __future__ import annotations

import shlex
from pathlib import PurePosixPath
from typing import Any

from compose_farm.plugins import HookContext, Plugin, PluginError


class AgenixPlugin(Plugin):
    """Pass host-decrypted env files to compose and check that secrets exist."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Validate ``secrets_dir`` and the per-stack secrets."""
        super().__init__(options)
        unknown = sorted(set(options) - {"secrets_dir", "stacks"})
        if unknown:
            msg = f"unknown option(s): {', '.join(unknown)}"
            raise PluginError(msg)
        secrets_dir = options.get("secrets_dir", "/run/agenix")
        if not isinstance(secrets_dir, str):
            msg = "secrets_dir must be a string"
            raise PluginError(msg)
        if "stacks" not in options:
            msg = "stacks is required (stack name -> secret files)"
            raise PluginError(msg)
        stacks = options["stacks"]
        if not isinstance(stacks, dict):
            msg = "stacks must map stack names to secret files"
            raise PluginError(msg)
        base = PurePosixPath(secrets_dir)
        self.env: dict[str, list[str]] = {}
        self.files: dict[str, list[str]] = {}
        for stack, spec in stacks.items():
            env, files = _parse_stack(stack, spec)
            self.env[stack] = [str(base / name) for name in env]
            self.files[stack] = [str(base / name) for name in files]

    def compose_args(self, ctx: HookContext) -> list[str]:
        """Add ``--env-file`` per secret, keeping the stack's own ``.env`` first."""
        env_files = self.env.get(ctx.stack, [])
        if not env_files:
            return []
        args: list[str] = []
        # Any --env-file stops compose from reading .env implicitly, so pass it explicitly.
        # compose_dir is local wherever cf runs (compose-farm parses compose files there).
        if (ctx.cfg.get_stack_dir(ctx.stack) / ".env").exists():
            args += ["--env-file", ".env"]
        for path in env_files:
            args += ["--env-file", path]
        return args

    async def preflight(self, ctx: HookContext) -> list[str]:
        """Report secrets that are missing or unreadable on ctx.host (one SSH call)."""
        paths = [*self.env.get(ctx.stack, []), *self.files.get(ctx.stack, [])]
        if not paths:
            return []
        quoted = " ".join(shlex.quote(path) for path in paths)
        script = f'for f in {quoted}; do [ -r "$f" ] || printf "%s\\n" "$f"; done'
        result = await ctx.run(script, stream=False)
        return [f"secret {path} is missing or unreadable" for path in result.stdout.splitlines()]


def _parse_stack(stack: str, spec: object) -> tuple[list[str], list[str]]:
    """Normalize ``name``, ``[names]``, or ``{env: ..., files: ...}`` to (env, files)."""
    if isinstance(spec, dict):
        keys: dict[str, object] = {str(key): value for key, value in spec.items()}
        unknown = sorted(set(keys) - {"env", "files"})
        if unknown:
            msg = f"stacks.{stack}: unknown key(s) {', '.join(unknown)} (use env, files)"
            raise PluginError(msg)
        env = _names(f"stacks.{stack}.env", keys.get("env", []))
        files = _names(f"stacks.{stack}.files", keys.get("files", []))
        return env, files
    return _names(f"stacks.{stack}", spec), []


def _names(where: str, value: object) -> list[str]:
    """Accept one file name or a list of file names."""
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        msg = f"{where} must be a file name or a list of file names"
        raise PluginError(msg)
    return [str(item) for item in items]
