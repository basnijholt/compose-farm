"""Builtin ``sync`` plugin: rsync each stack directory to its host before starting it.

Replaces NFS for compose files. ``compose_dir`` must exist locally at the same
path it has on the hosts; ``compose_dir/<stack>/`` is copied to that path on
the target host before every start. The local copy is the source of truth:
files that differ are overwritten on the host, so exclude runtime data and
host-specific files.

Example::

    plugins:
      sync:
        excludes: [".git"]
        delete: false  # true removes host files missing locally, e.g. bind-mounted data
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from compose_farm.executor import build_ssh_command, is_local
from compose_farm.plugins import HookContext, Plugin, PluginError
from compose_farm.ssh_keys import get_ssh_auth_sock

if TYPE_CHECKING:
    from pathlib import Path

    from compose_farm.config import Host


class SyncPlugin(Plugin):
    """Copy the local stack directory to the target host before it starts."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Validate ``excludes`` (rsync patterns) and ``delete`` (default false)."""
        super().__init__(options)
        unknown = sorted(set(options) - {"excludes", "delete"})
        if unknown:
            msg = f"unknown option(s): {', '.join(unknown)}"
            raise PluginError(msg)
        excludes = options.get("excludes", [])
        if not isinstance(excludes, list) or not all(isinstance(p, str) for p in excludes):
            msg = "excludes must be a list of strings"
            raise PluginError(msg)
        delete = options.get("delete", False)
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
        command = shlex.join(
            _rsync_argv(host, stack_dir, excludes=self.excludes, delete=self.delete)
        )
        # Use the same agent auto-detection as compose-farm's own SSH connections
        if sock := get_ssh_auth_sock():
            command = f"SSH_AUTH_SOCK={shlex.quote(sock)} {command}"
        await ctx.run_local(command, stream=False)


def _rsync_argv(host: Host, stack_dir: Path, *, excludes: list[str], delete: bool) -> list[str]:
    """Build the rsync command copying stack_dir to the same path on host over compose-farm's SSH."""
    ssh = build_ssh_command(host, "")[:-2]  # Drop "user@address" and the remote command
    argv = ["rsync", "-az", "-e", shlex.join(ssh)]
    if delete:
        argv.append("--delete")
    for pattern in excludes:
        argv.extend(["--exclude", pattern])
    address = f"[{host.address}]" if ":" in host.address else host.address  # IPv6
    argv.extend([f"{stack_dir}/", f"{host.user}@{address}:{stack_dir}/"])
    return argv
