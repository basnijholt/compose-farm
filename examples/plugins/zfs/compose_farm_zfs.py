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

- First deploy: the dataset is created, unless a copy exists on another host
  (then the start is refused instead of starting empty). Hosts that cannot be
  checked also block the start.
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
there (once no host runs the stack anymore), and a migration moves nothing
because every host sees the same data.

Safety rules: copies are matched by snapshot guid, never by name; snapshots on
the target are only dropped when they hold no data (``written == 0``);
datasets with children are not moved; anything that cannot be checked stops
the operation before it changes data.
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from typing import Any, NamedTuple

from compose_farm.executor import build_ssh_command, is_local
from compose_farm.plugins import HookContext, Plugin, PluginError
from compose_farm.ssh_keys import get_ssh_auth_sock
from compose_farm.state import get_stack_host

SNAPSHOT_PREFIX = "cf-"


class Snapshot(NamedTuple):
    """A snapshot of a dataset on one host."""

    name: str  # The part after "@"
    guid: str  # Identical on every copy of the same snapshot
    written: int  # Bytes changed since the previous snapshot


class Zfs:
    """Runs zfs (and one docker) command on hosts through a hook context."""

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
        """Whether dataset ``name`` exists on host; any other failure raises."""
        result = await self.ctx.run(
            self._zfs("list", "-H", "-o", "name", name), host=host, stream=False, check=False
        )
        if result.success:
            return True
        if "dataset does not exist" in result.stderr:
            return False
        detail = result.stderr.strip() or f"exit {result.exit_code}"
        msg = f"cannot check {name} on {host}: {detail}"
        raise PluginError(msg)

    async def snapshots(self, host: str, name: str) -> list[Snapshot]:
        """Snapshots of ``name`` on host, oldest first."""
        out = await self._run(
            host, "list", "-Hp", "-t", "snapshot", "-o", "name,guid,written", "-s", "creation", "-d", "1", name
        )  # fmt: skip
        snapshots = []
        for line in out.splitlines():
            full_name, guid, written = line.split("\t")
            snapshots.append(Snapshot(full_name.split("@", 1)[1], guid, int(written)))
        return snapshots

    async def children(self, host: str, name: str) -> list[str]:
        """Datasets below ``name`` on host."""
        out = await self._run(
            host, "list", "-H", "-o", "name", "-r", "-t", "filesystem,volume", name
        )
        return [line for line in out.splitlines() if line and line != name]

    async def running(self, host: str, stack: str) -> bool:
        """Whether any container of the stack's compose project runs on host."""
        label = shlex.quote(f"label=com.docker.compose.project={stack}")
        result = await self.ctx.run(f"docker ps -q --filter {label}", host=host, stream=False)
        return bool(result.stdout.strip())

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
        if snap not in [s.name for s in await self.snapshots(dst, name)]:
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
        # compose-farm forgets a stack's host on `cf down`, so unless the state says the
        # stack runs here, make sure no other host holds a (possibly newer) copy.
        # Multi-host stacks have one dataset per host.
        single_host = len(ctx.cfg.get_hosts(ctx.stack)) == 1
        if single_host and get_stack_host(ctx.cfg, ctx.stack) != ctx.host:
            await _refuse_if_elsewhere(zfs, name, ctx)
        if not await zfs.exists(ctx.host, name):
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
            snapshots = await zfs.snapshots(ctx.host, name)
            ours = [s.name for s in snapshots if s.name.startswith(SNAPSHOT_PREFIX)]
            for snap in ours[:-1]:
                await zfs.destroy(ctx.host, f"{name}@{snap}")

    async def on_stack_removed(self, ctx: HookContext) -> None:
        """Retire the dataset of a stack that was removed from the config."""
        name = self._dataset(ctx)
        if name is None:
            return
        zfs = self.tool(ctx)
        if not self.storage_host:
            await self._retire(zfs, ctx.host, name)
            return
        # Shared data: a multi-host stack stops host by host, so keep the dataset while
        # any other host still runs the stack (the last one to stop retires it)
        for other in ctx.cfg.hosts:
            if other != ctx.host and await zfs.running(other, ctx.stack):
                return
        await self._retire(zfs, self.storage_host, name)

    async def _retire(self, zfs: Zfs, host: str, name: str) -> None:
        if not await zfs.exists(host, name):
            return
        if self.retire == "destroy":
            await zfs.destroy(host, name)
        else:
            await zfs.rename(host, name, f"{name}.retired-{_timestamp()}")


async def _send(zfs: Zfs, name: str, src: str, dst: str) -> None:
    """Snapshot on src and bring dst up to date (full or incremental)."""
    if children := await zfs.children(src, name):
        msg = (
            f"{name} on {src} has child datasets ({', '.join(children)}); "
            "this example only moves single datasets"
        )
        raise PluginError(msg)
    base, extra = await _plan_incremental(zfs, name, src, dst)
    snap = f"{SNAPSHOT_PREFIX}{_timestamp()}"
    await zfs.snapshot(src, name, snap)
    for old in extra:
        await zfs.destroy(dst, f"{name}@{old}")
    await zfs.send(src, dst, name, snap, base)


async def _plan_incremental(
    zfs: Zfs, name: str, src: str, dst: str
) -> tuple[str | None, list[str]]:
    """Return the incremental base (None for a full send) and dst snapshots to drop first.

    The base is the newest dst snapshot whose guid also exists on src; equal names
    alone prove nothing (independent auto-snapshots share names). Snapshots taken on
    dst after the base block the receive; they are only dropped when they hold no
    data (e.g. taken by sanoid on the idle copy), otherwise nothing is changed.
    """
    if not await zfs.exists(dst, name):
        return None, []
    source_names = {s.guid: s.name for s in await zfs.snapshots(src, name)}
    target = await zfs.snapshots(dst, name)
    shared = [i for i, s in enumerate(target) if s.guid in source_names]
    if not shared:
        msg = f"{name} exists on {src} and {dst} without a common snapshot; resolve by hand"
        raise PluginError(msg)
    base = target[shared[-1]]
    extra = target[shared[-1] + 1 :]
    if changed := [s.name for s in extra if s.written > 0]:
        msg = (
            f"{name} changed on {dst} after {base.name} (snapshots {', '.join(changed)} "
            "hold data); resolve by hand"
        )
        raise PluginError(msg)
    return source_names[base.guid], [s.name for s in extra]


async def _refuse_if_elsewhere(zfs: Zfs, name: str, ctx: HookContext) -> None:
    """Refuse to go on while another host holds a copy (or cannot be checked)."""
    for other in ctx.cfg.hosts:
        if other != ctx.host and await zfs.exists(other, name):
            msg = (
                f"{name} already exists on {other}; refusing to start {ctx.stack} on "
                f"{ctx.host} next to it. Start the stack on {other}, or move or remove "
                "one copy by hand."
            )
            raise PluginError(msg)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
