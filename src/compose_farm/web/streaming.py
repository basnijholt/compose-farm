"""Streaming executor adapter for web UI."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from compose_farm.config import Config

# In-memory task registry
tasks: dict[str, dict[str, Any]] = {}


async def stream_to_task(task_id: str, message: str) -> None:
    """Send a message to a task's output buffer."""
    if task_id in tasks:
        tasks[task_id]["output"].append(message)


async def run_compose_streaming(
    config: Config,
    service: str,
    command: str,
    task_id: str,
) -> None:
    """Run a compose command with output streamed to task buffer."""
    from compose_farm.executor import is_local

    try:
        host_name = config.get_hosts(service)[0]
        host = config.hosts[host_name]
        compose_path = config.get_compose_path(service)

        if not compose_path:
            await stream_to_task(
                task_id, f"\x1b[31mError: Compose file not found for {service}\x1b[0m\r\n"
            )
            tasks[task_id]["status"] = "failed"
            return

        # Build the full command
        if "&&" in command:
            # Handle compound commands like "down && docker compose up -d"
            full_command = f"cd {compose_path.parent} && docker compose {command}"
        else:
            full_command = f"docker compose -f {compose_path} {command}"

        await stream_to_task(task_id, f"\x1b[36m[{service}]\x1b[0m Running: {command}\r\n")

        if is_local(host):
            await _run_local_streaming(full_command, task_id, service)
        else:
            await _run_ssh_streaming(host, full_command, task_id, service)

        tasks[task_id]["status"] = "completed"

    except Exception as e:
        await stream_to_task(task_id, f"\x1b[31mError: {e}\x1b[0m\r\n")
        tasks[task_id]["status"] = "failed"


async def _run_local_streaming(command: str, task_id: str, prefix: str) -> int:
    """Run command locally with streaming output."""
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    if proc.stdout:
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace")
            await stream_to_task(task_id, f"\x1b[36m[{prefix}]\x1b[0m {text}")

    await proc.wait()
    return proc.returncode or 0


async def _run_ssh_streaming(
    host: Any,
    command: str,
    task_id: str,
    prefix: str,
) -> int:
    """Run command via SSH with streaming output."""
    import asyncssh

    try:
        async with asyncssh.connect(
            host.address,
            username=host.user,
            port=host.port,
            known_hosts=None,
        ) as conn:
            async with conn.create_process(command) as proc:
                if proc.stdout:
                    async for line in proc.stdout:
                        await stream_to_task(task_id, f"\x1b[36m[{prefix}]\x1b[0m {line}")
                if proc.stderr:
                    async for line in proc.stderr:
                        await stream_to_task(
                            task_id, f"\x1b[36m[{prefix}]\x1b[0m \x1b[33m{line}\x1b[0m"
                        )
                await proc.wait()
                return proc.exit_status or 0
    except Exception as e:
        await stream_to_task(task_id, f"\x1b[31mSSH Error: {e}\x1b[0m\r\n")
        return 1


async def run_apply_streaming(config: Config, task_id: str) -> None:
    """Run cf apply with streaming output."""
    from compose_farm.state import (
        get_orphaned_services,
        get_services_needing_migration,
        get_services_not_in_state,
    )

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
