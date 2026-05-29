"""Built-in plugin to sync local stack trees to target hosts."""

from __future__ import annotations

import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from compose_farm.console import console
from compose_farm.plugins.types import (
    HookContext,
    HookEvent,
    HookPolicy,
    HookRegistration,
    HookResult,
)

if TYPE_CHECKING:
    from compose_farm.config import Host

_PLUGIN_NAME = "sync"
_DEFAULT_EVENTS = frozenset({HookEvent.PRE_UP.value, HookEvent.PRE_APPLY.value})
_DEFAULT_SSH_PORT = 22


class SyncPlugin:
    """Sync local stack directories into compose_dir on target hosts.

    Config shape (under plugin_config.sync):

    plugin_config:
      sync:
        source_dir: "/path/to/stacks"
        events: ["pre_up", "pre_apply"]
        excludes: [".git", "*.tmp"]
        delete: true
        rsync_flags: ["-az"]
    """

    def register_hooks(self) -> list[HookRegistration]:
        """Register one handler per event; handler no-ops if event is disabled."""
        return [
            HookRegistration(
                plugin=_PLUGIN_NAME,
                hook="sync-tree",
                event=event,
                handler=self._handle_event,
                policy=HookPolicy.BLOCKING,
            )
            for event in HookEvent
        ]

    def _handle_event(self, context: HookContext) -> HookResult:  # noqa: PLR0911
        """Sync configured source tree for selected stacks/hosts."""
        plugin_cfg = context.config.get_plugin_config(_PLUGIN_NAME)
        enabled_events = _event_set(plugin_cfg)
        if context.event.value not in enabled_events:
            return HookResult(
                plugin=_PLUGIN_NAME,
                hook="sync-tree",
                event=context.event,
                success=True,
                message="event not enabled",
            )

        rsync = shutil.which("rsync")
        if not rsync:
            return HookResult(
                plugin=_PLUGIN_NAME,
                hook="sync-tree",
                event=context.event,
                success=False,
                message="rsync executable not found",
            )

        source_root = _source_root(context, plugin_cfg)
        if not source_root.exists() or not source_root.is_dir():
            return HookResult(
                plugin=_PLUGIN_NAME,
                hook="sync-tree",
                event=context.event,
                success=False,
                message=f"source_dir does not exist: {source_root}",
            )

        targets = _targets_for_context(context)
        if not targets:
            return HookResult(
                plugin=_PLUGIN_NAME,
                hook="sync-tree",
                event=context.event,
                success=True,
                message="no targets to sync",
            )

        rsync_flags = _rsync_flags(plugin_cfg)
        excludes = _excludes(plugin_cfg)
        delete_enabled = bool(plugin_cfg.get("delete", True))
        show_progress = len(targets) > 1 and not context.dry_run

        synced: list[str] = []
        progress_context = (
            Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                transient=True,
                console=console,
            )
            if show_progress
            else nullcontext(None)
        )

        with progress_context as progress:
            task_id = progress.add_task("Syncing stacks", total=len(targets)) if progress else None

            for stack, host_name in targets:
                if progress and task_id is not None:
                    progress.update(task_id, description=f"Syncing {stack}@{host_name}")

                source_dir = source_root / stack
                if not source_dir.exists() or not source_dir.is_dir():
                    return HookResult(
                        plugin=_PLUGIN_NAME,
                        hook="sync-tree",
                        event=context.event,
                        success=False,
                        message=f"stack source missing: {source_dir}",
                    )

                host = context.config.hosts[host_name]
                dest_dir = str(context.config.get_stack_dir(stack))

                if not _ensure_destination_dir(host, dest_dir, dry_run=context.dry_run):
                    return HookResult(
                        plugin=_PLUGIN_NAME,
                        hook="sync-tree",
                        event=context.event,
                        success=False,
                        message=f"failed to create destination dir on host {host_name}: {dest_dir}",
                    )

                if not _run_rsync(
                    rsync,
                    source_dir=source_dir,
                    host=host,
                    dest_dir=dest_dir,
                    dry_run=context.dry_run,
                    delete_enabled=delete_enabled,
                    rsync_flags=rsync_flags,
                    excludes=excludes,
                ):
                    return HookResult(
                        plugin=_PLUGIN_NAME,
                        hook="sync-tree",
                        event=context.event,
                        success=False,
                        message=f"sync failed for {stack}@{host_name}",
                    )

                synced.append(f"{stack}@{host_name}")
                if progress and task_id is not None:
                    progress.advance(task_id)

        return HookResult(
            plugin=_PLUGIN_NAME,
            hook="sync-tree",
            event=context.event,
            success=True,
            message=", ".join(synced),
        )


def _source_root(context: HookContext, plugin_cfg: dict[str, object]) -> Path:
    """Resolve source root for stack directories."""
    source_raw = plugin_cfg.get("source_dir")
    if isinstance(source_raw, str) and source_raw:
        return Path(source_raw).expanduser()

    if context.config.config_path:
        return context.config.config_path.parent

    return Path.cwd()


def _event_set(plugin_cfg: dict[str, object]) -> set[str]:
    """Get enabled event names for sync execution."""
    configured = plugin_cfg.get("events")
    if configured is None:
        return set(_DEFAULT_EVENTS)
    if not isinstance(configured, list) or not all(isinstance(item, str) for item in configured):
        return set()
    return set(cast("list[str]", configured))


def _rsync_flags(plugin_cfg: dict[str, object]) -> list[str]:
    """Get rsync flags from plugin config with defaults."""
    flags = plugin_cfg.get("rsync_flags")
    if isinstance(flags, list) and all(isinstance(item, str) for item in flags):
        return list(cast("list[str]", flags))
    return ["-az"]


def _excludes(plugin_cfg: dict[str, object]) -> list[str]:
    """Get rsync exclude patterns from plugin config."""
    excludes = plugin_cfg.get("excludes")
    if isinstance(excludes, list) and all(isinstance(item, str) for item in excludes):
        return list(cast("list[str]", excludes))
    return []


def _targets_for_context(context: HookContext) -> list[tuple[str, str]]:
    """Compute unique sync targets for a lifecycle event."""
    targets: list[tuple[str, str]] = []

    if context.event == HookEvent.PRE_UP:
        if context.target_host:
            targets.append((context.stack, context.target_host))
        else:
            targets.extend(
                (context.stack, host) for host in context.config.get_hosts(context.stack)
            )
    elif context.event == HookEvent.PRE_APPLY:
        for stack in context.config.stacks:
            targets.extend((stack, host) for host in context.config.get_hosts(stack))

    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for target in targets:
        if target not in seen:
            seen.add(target)
            deduped.append(target)
    return deduped


def _ensure_destination_dir(host: Host, dest_dir: str, *, dry_run: bool) -> bool:
    """Ensure destination directory exists on local or remote host."""
    if dry_run:
        return True

    if host.address in {"local", "localhost", "127.0.0.1", "::1"}:
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        return True

    command = ["ssh"]
    if host.port != _DEFAULT_SSH_PORT:
        command.extend(["-p", str(host.port)])
    command.append(f"{host.user}@{host.address}")
    command.append(f"mkdir -p {dest_dir!r}")
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    return result.returncode == 0


def _run_rsync(
    rsync: str,
    *,
    source_dir: Path,
    host: Host,
    dest_dir: str,
    dry_run: bool,
    delete_enabled: bool,
    rsync_flags: list[str],
    excludes: list[str],
) -> bool:
    """Run rsync for one stack to one host destination."""
    if dry_run:
        return True

    args: list[str] = [rsync, *rsync_flags]
    if delete_enabled:
        args.append("--delete")
    for pattern in excludes:
        args.extend(["--exclude", pattern])

    source = f"{source_dir}/"
    if host.address in {"local", "localhost", "127.0.0.1", "::1"}:
        destination = f"{dest_dir}/"
    else:
        target = f"{host.user}@{host.address}:{dest_dir}/"
        destination = target
        if host.port != _DEFAULT_SSH_PORT:
            args.extend(["-e", f"ssh -p {host.port}"])

    args.extend([source, destination])
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    return result.returncode == 0
