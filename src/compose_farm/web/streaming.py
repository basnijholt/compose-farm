"""Streaming executor adapter for web UI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from compose_farm.executor import run_compose
from compose_farm.state import (
    get_orphaned_services,
    get_services_needing_migration,
    get_services_not_in_state,
)

if TYPE_CHECKING:
    from compose_farm.config import Config

# In-memory task registry
tasks: dict[str, dict[str, Any]] = {}


async def stream_to_task(task_id: str, message: str) -> None:
    """Send a message to a task's output buffer."""
    if task_id in tasks:
        tasks[task_id]["output"].append(message)


def make_task_callback(task_id: str):
    """Create an output callback that writes to a task buffer with ANSI colors."""

    async def callback(prefix: str, text: str, is_stderr: bool) -> None:
        if is_stderr:
            msg = f"\x1b[36m[{prefix}]\x1b[0m \x1b[33m{text}\x1b[0m"
        else:
            msg = f"\x1b[36m[{prefix}]\x1b[0m {text}"
        await stream_to_task(task_id, msg)

    return callback


async def run_compose_streaming(
    config: Config,
    service: str,
    command: str,
    task_id: str,
) -> None:
    """Run a compose command with output streamed to task buffer."""
    try:
        compose_path = config.get_compose_path(service)
        if not compose_path:
            await stream_to_task(
                task_id, f"\x1b[31mError: Compose file not found for {service}\x1b[0m\r\n"
            )
            tasks[task_id]["status"] = "failed"
            return

        await stream_to_task(task_id, f"\x1b[36m[{service}]\x1b[0m Running: {command}\r\n")

        callback = make_task_callback(task_id)
        result = await run_compose(config, service, command, output_callback=callback)

        tasks[task_id]["status"] = "completed" if result.success else "failed"

    except Exception as e:
        await stream_to_task(task_id, f"\x1b[31mError: {e}\x1b[0m\r\n")
        tasks[task_id]["status"] = "failed"


async def run_apply_streaming(config: Config, task_id: str) -> None:
    """Run cf apply with streaming output."""
    try:
        await stream_to_task(task_id, "\x1b[36m[apply]\x1b[0m Analyzing state...\r\n")

        orphaned = get_orphaned_services(config)
        migrations = get_services_needing_migration(config)
        missing = get_services_not_in_state(config)

        if not orphaned and not migrations and not missing:
            await stream_to_task(
                task_id, "\x1b[32m[apply]\x1b[0m Nothing to apply - reality matches config\r\n"
            )
            tasks[task_id]["status"] = "completed"
            return

        # Report what will be done
        if orphaned:
            await stream_to_task(
                task_id,
                f"\x1b[33m[apply]\x1b[0m Orphaned services: {', '.join(orphaned.keys())}\r\n",
            )
        if migrations:
            await stream_to_task(
                task_id, f"\x1b[36m[apply]\x1b[0m Services to migrate: {', '.join(migrations)}\r\n"
            )
        if missing:
            await stream_to_task(
                task_id, f"\x1b[32m[apply]\x1b[0m Services to start: {', '.join(missing)}\r\n"
            )

        # Stop orphaned services
        for svc in orphaned:
            await stream_to_task(task_id, f"\x1b[33m[apply]\x1b[0m Stopping orphaned: {svc}\r\n")
            await run_compose_streaming(config, svc, "down", task_id)

        # Migrate and start services
        for svc in migrations + missing:
            await stream_to_task(task_id, f"\x1b[32m[apply]\x1b[0m Starting: {svc}\r\n")
            await run_compose_streaming(config, svc, "up -d", task_id)

        tasks[task_id]["status"] = "completed"

    except Exception as e:
        await stream_to_task(task_id, f"\x1b[31m[apply] Error: {e}\x1b[0m\r\n")
        tasks[task_id]["status"] = "failed"
