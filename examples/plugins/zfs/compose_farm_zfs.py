"""Example compose-farm plugin: one ZFS dataset per stack that moves with the stack.

Instead of sharing data over NFS, every host keeps its stacks' data on local
ZFS. Each stack gets ``<dataset>/<stack>``; give the parent a mountpoint (for
example ``zfs set mountpoint=/mnt/data tank/data``) and bind-mount
``/mnt/data/<stack>/...`` in your compose files::

    plugins:
      zfs:
        dataset: tank/data   # parent dataset, must exist on every host
        zfs: zfs             # command prefix, e.g. "sudo zfs"
        stacks: [mealie]     # optional: only these stacks (default: all)
        retire: rename       # or "destroy"
        storage_host: nas    # optional, see below

What happens:

- First deploy: the dataset is created, unless it already exists on another
  reachable host (then the start is refused instead of starting empty).
- Migration: while the old host still runs the stack, a snapshot is sent to
  the new host (full the first time, incremental after). Once the old host is
  stopped, a final incremental is sent, so downtime is short. After the stack
  is up, the old copy is renamed to ``<stack>.retired-<time>`` (or destroyed)
  and older ``cf-`` snapshots on the new host are pruned.
- Stack removed from config: its dataset is retired the same way.

Transfers are relayed through the machine running cf (``ssh old zfs send |
ssh new zfs recv``), so hosts need no SSH access to each other.

With ``storage_host``, all datasets live on that one host (a NAS that shares
``/mnt/data`` with the others over NFS). Datasets are created and retired
there, and a migration moves nothing because every host sees the same data.
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from typing import Any

from compose_farm.console import print_warning
from compose_farm.executor import build_ssh_command, is_local
from compose_farm.plugins import HookContext, Plugin, PluginError
from compose_farm.ssh_keys import get_ssh_auth_sock

SNAPSHOT_PREFIX = "cf-"


class Zfs:
    """Runs zfs commands on hosts through a hook context."""

    def __init__(self, ctx: HookContext, command: str) -> None:
        """Use ``command`` (e.g. ``zfs`` or ``sudo zfs``) on the context's hosts."""
        self.ctx = ctx
        self.command = command

    def _zfs(self, *args: str) -> str:
        return f"{self.command} {shlex.join(args)}"

    async def _run(self, host: str, *args: str) -> str:
        result = await self.ctx.run(self._zfs(*args), host=host, stream=False)
        return result.stdout

    async def exists(self, host: str, name: str) -> bool:
        """Whether dataset ``name`` exists on host. SSH failures raise."""
        check = self._zfs("list", "-H", "-o", "name", name)
        script = f"if {check} >/dev/null 2>&1; then echo yes; else echo no; fi"
        result = await self.ctx.run(script, host=host, stream=False)
        return result.stdout.strip() == "yes"

    async def snapshots(self, host: str, name: str) -> list[str]:
        """Snapshot names of ``name`` on host, oldest first."""
        out = await self._run(
            host, "list", "-H", "-t", "snapshot", "-o", "name", "-s", "creation", "-d", "1", name
        )
        return [line.split("@", 1)[1] for line in out.splitlines() if "@" in line]

    async def snapshot(self, host: str, name: str, snap: str) -> None:
        """Take snapshot ``name@snap`` on host."""
        await self._run(host, "snapshot", f"{name}@{snap}")

    async def create(self, host: str, name: str) -> None:
        """Create dataset ``name`` on host."""
        await self._run(host, "create", "-p", name)

    async def rename(self, host: str, name: str, new: str) -> None:
        """Rename dataset ``name`` to ``new`` on host."""
        await self._run(host, "rename", name, new)

    async def destroy(self, host: str, target: str) -> None:
        """Destroy a snapshot (``name@snap``) or a dataset with its snapshots."""
        await self._run(host, "destroy", *([] if "@" in target else ["-r"]), target)

    async def send(self, src: str, dst: str, name: str, snap: str, base: str | None) -> None:
        """Send ``name@snap`` from src to dst (incremental from ``base`` if given)."""
        send = self._zfs("send", *(["-I", f"@{base}"] if base else []), f"{name}@{snap}")
        recv = self._zfs("recv", *(["-F"] if base else []), name)
        pipeline = f"{self._on(src, send)} | {self._on(dst, recv)}"
        if sock := get_ssh_auth_sock():
            pipeline = f"export SSH_AUTH_SOCK={shlex.quote(sock)}; {pipeline}"
        await self.ctx.run_local(pipeline, stream=False)
        # sh has no pipefail, so confirm the snapshot actually arrived
        if snap not in await self.snapshots(dst, name):
            msg = f"{name}@{snap} did not arrive on {dst}"
            raise PluginError(msg)

    def _on(self, host_name: str, command: str) -> str:
        host = self.ctx.cfg.hosts[host_name]
        return command if is_local(host) else shlex.join(build_ssh_command(host, command))


class ZfsPlugin(Plugin):
    """Create, move, and retire a ZFS dataset per stack."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Validate ``dataset``, ``zfs``, ``stacks``, ``retire``, and ``storage_host``."""
        super().__init__(options)
        unknown = sorted(set(options) - {"dataset", "zfs", "stacks", "retire", "storage_host"})
        if unknown:
            msg = f"unknown option(s): {', '.join(unknown)}"
            raise PluginError(msg)
        dataset = options.get("dataset")
        if not isinstance(dataset, str) or not dataset.strip("/"):
            msg = "dataset is required (parent dataset, e.g. tank/data)"
            raise PluginError(msg)
        command = options.get("zfs", "zfs")
        if not isinstance(command, str):
            msg = "zfs must be a command string, e.g. 'sudo zfs'"
            raise PluginError(msg)
        stacks = options.get("stacks")
        if stacks is not None and not (
            isinstance(stacks, list) and all(isinstance(s, str) for s in stacks)
        ):
            msg = "stacks must be a list of stack names"
            raise PluginError(msg)
        retire = options.get("retire", "rename")
        if retire not in ("rename", "destroy"):
            msg = "retire must be 'rename' or 'destroy'"
            raise PluginError(msg)
        storage_host = options.get("storage_host")
        if storage_host is not None and not isinstance(storage_host, str):
            msg = "storage_host must be a host name"
            raise PluginError(msg)
        self.dataset = dataset.strip("/")
        self.command = command
        self.stacks: set[str] | None = None if stacks is None else {str(s) for s in stacks}
        self.retire = retire
        self.storage_host: str | None = storage_host

    def tool(self, ctx: HookContext) -> Zfs:
        """ZFS command runner for a hook call (replaced in tests)."""
        return Zfs(ctx, self.command)

    def _dataset(self, ctx: HookContext) -> str | None:
        """The stack's dataset, or None if this stack is not managed."""
        if self.stacks is not None and ctx.stack not in self.stacks:
            return None
        return f"{self.dataset}/{ctx.stack}"

    async def preflight(self, ctx: HookContext) -> list[str]:
        """The parent dataset must exist where the stack's dataset lives."""
        if self._dataset(ctx) is None:
            return []
        if self.storage_host and self.storage_host not in ctx.cfg.hosts:
            return [f"storage_host {self.storage_host!r} is not in the config"]
        host = self.storage_host or ctx.host
        if await self.tool(ctx).exists(host, self.dataset):
            return []
        return [f"parent dataset {self.dataset} not found on {host} (is ZFS set up there?)"]

    async def before_up(self, ctx: HookContext) -> None:
        """Copy the dataset from the previous host, or create it on first deploy."""
        name = self._dataset(ctx)
        if name is None:
            return
        zfs = self.tool(ctx)
        if self.storage_host:
            if not await zfs.exists(self.storage_host, name):
                await zfs.create(self.storage_host, name)
            return
        if ctx.source_host:
            if ctx.source_host not in ctx.cfg.hosts:
                msg = (
                    f"{name} is on {ctx.source_host}, which is no longer in the config; "
                    "move the dataset by hand"
                )
                raise PluginError(msg)
            if await zfs.exists(ctx.source_host, name):
                await _send(zfs, name, ctx.source_host, ctx.host)
                return
        if await zfs.exists(ctx.host, name):
            return
        if len(ctx.cfg.get_hosts(ctx.stack)) == 1:  # Multi-host stacks have one per host
            await _refuse_if_elsewhere(zfs, name, ctx)
        await zfs.create(ctx.host, name)

    async def after_source_stopped(self, ctx: HookContext) -> None:
        """Send the changes made since the live copy, now that the source is stopped."""
        name = self._dataset(ctx)
        if name is None or not ctx.source_host or self.storage_host:
            return
        zfs = self.tool(ctx)
        if await zfs.exists(ctx.source_host, name):
            await _send(zfs, name, ctx.source_host, ctx.host)

    async def after_up(self, ctx: HookContext) -> None:
        """Retire the old copy after a migration and prune transfer snapshots."""
        name = self._dataset(ctx)
        if name is None or self.storage_host:
            return
        zfs = self.tool(ctx)
        if ctx.source_host and ctx.source_host in ctx.cfg.hosts:
            await self._retire(zfs, ctx.source_host, name)
        if await zfs.exists(ctx.host, name):
            ours = [s for s in await zfs.snapshots(ctx.host, name) if s.startswith(SNAPSHOT_PREFIX)]
            for snap in ours[:-1]:
                await zfs.destroy(ctx.host, f"{name}@{snap}")

    async def on_stack_removed(self, ctx: HookContext) -> None:
        """Retire the dataset of a stack that was removed from the config."""
        name = self._dataset(ctx)
        if name is not None:
            await self._retire(self.tool(ctx), self.storage_host or ctx.host, name)

    async def _retire(self, zfs: Zfs, host: str, name: str) -> None:
        if not await zfs.exists(host, name):
            return
        if self.retire == "destroy":
            await zfs.destroy(host, name)
        else:
            await zfs.rename(host, name, f"{name}.retired-{_timestamp()}")


async def _send(zfs: Zfs, name: str, src: str, dst: str) -> None:
    """Snapshot on src and bring dst up to date (full or incremental)."""
    snap = f"{SNAPSHOT_PREFIX}{_timestamp()}"
    await zfs.snapshot(src, name, snap)
    base = None
    if await zfs.exists(dst, name):
        on_source = set(await zfs.snapshots(src, name))
        on_target = await zfs.snapshots(dst, name)
        common = [s for s in on_target if s in on_source]
        if not common:
            msg = f"{name} exists on {src} and {dst} without a common snapshot; resolve by hand"
            raise PluginError(msg)
        base = common[-1]
        # Snapshots taken on dst after the base (e.g. by sanoid, while the stack is not
        # running there) make the incremental receive fail. They only hold data that
        # came from src, since dst does not run the stack.
        for extra in on_target[on_target.index(base) + 1 :]:
            await zfs.destroy(dst, f"{name}@{extra}")
    await zfs.send(src, dst, name, snap, base)


async def _refuse_if_elsewhere(zfs: Zfs, name: str, ctx: HookContext) -> None:
    """Refuse to create an empty dataset while a copy exists on another host.

    compose-farm forgets a stack's host on `cf down`, so a missing source host
    does not prove this is the first deploy.
    """
    for other in ctx.cfg.hosts:
        if other == ctx.host:
            continue
        try:
            found = await zfs.exists(other, name)
        except PluginError as e:
            print_warning(f"[{ctx.stack}] zfs: could not check {other} for {name}: {e}")
            continue
        if found:
            msg = (
                f"{name} already exists on {other}; refusing to create an empty one on "
                f"{ctx.host}. Start the stack on {other}, or move the dataset by hand."
            )
            raise PluginError(msg)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
