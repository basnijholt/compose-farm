"""Streaming executor adapter for web UI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from compose_farm.executor import check_service_running, run_compose
from compose_farm.state import (
    get_orphaned_services,
    get_services_needing_migration,
    get_services_not_in_state,
    load_state,
    save_state,
)

if TYPE_CHECKING:
    from compose_farm.config import Config

# ANSI color codes
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
RESET = "\x1b[0m"

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
            msg = f"{CYAN}[{prefix}]{RESET} {YELLOW}{text}{RESET}"
        else:
            msg = f"{CYAN}[{prefix}]{RESET} {text}"
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
                task_id, f"{RED}Error: Compose file not found for {service}{RESET}\r\n"
            )
            tasks[task_id]["status"] = "failed"
            return

        await stream_to_task(task_id, f"{CYAN}[{service}]{RESET} Running: {command}\r\n")

        callback = make_task_callback(task_id)
        result = await run_compose(config, service, command, output_callback=callback)

        tasks[task_id]["status"] = "completed" if result.success else "failed"

    except Exception as e:
        await stream_to_task(task_id, f"{RED}Error: {e}{RESET}\r\n")
        tasks[task_id]["status"] = "failed"


async def run_apply_streaming(config: Config, task_id: str) -> None:
    """Run cf apply with streaming output."""
    try:
        await stream_to_task(task_id, f"{CYAN}[apply]{RESET} Analyzing state...\r\n")

        orphaned = get_orphaned_services(config)
        migrations = get_services_needing_migration(config)
        missing = get_services_not_in_state(config)

        if not orphaned and not migrations and not missing:
            await stream_to_task(
                task_id, f"{GREEN}[apply]{RESET} Nothing to apply - reality matches config\r\n"
            )
            tasks[task_id]["status"] = "completed"
            return

        # Report what will be done
        if orphaned:
            await stream_to_task(
                task_id,
                f"{YELLOW}[apply]{RESET} Orphaned services: {', '.join(orphaned.keys())}\r\n",
            )
        if migrations:
            await stream_to_task(
                task_id, f"{CYAN}[apply]{RESET} Services to migrate: {', '.join(migrations)}\r\n"
            )
        if missing:
            await stream_to_task(
                task_id, f"{GREEN}[apply]{RESET} Services to start: {', '.join(missing)}\r\n"
            )

        # Stop orphaned services
        for svc in orphaned:
            await stream_to_task(task_id, f"{YELLOW}[apply]{RESET} Stopping orphaned: {svc}\r\n")
            await run_compose_streaming(config, svc, "down", task_id)

        # Migrate and start services
        for svc in migrations + missing:
            await stream_to_task(task_id, f"{GREEN}[apply]{RESET} Starting: {svc}\r\n")
            await run_compose_streaming(config, svc, "up -d", task_id)

        tasks[task_id]["status"] = "completed"

    except Exception as e:
        await stream_to_task(task_id, f"{RED}[apply] Error: {e}{RESET}\r\n")
        tasks[task_id]["status"] = "failed"


async def run_refresh_streaming(config: Config, task_id: str) -> None:
    """Run cf refresh with streaming output."""
    try:
        await stream_to_task(task_id, f"{CYAN}[refresh]{RESET} Discovering running services...\r\n")

        current_state = load_state(config)
        discovered: dict[str, str | list[str]] = {}

        # Check each service
        for service in config.services:
            assigned_hosts = config.get_hosts(service)

            if config.is_multi_host(service):
                # Multi-host: find all hosts where running
                running_hosts = []
                for host_name in assigned_hosts:
                    if await check_service_running(config, service, host_name):
                        running_hosts.append(host_name)
                if running_hosts:
                    discovered[service] = running_hosts
                    await stream_to_task(
                        task_id,
                        f"{GREEN}[refresh]{RESET} {service}: running on {', '.join(running_hosts)}\r\n",
                    )
                else:
                    await stream_to_task(
                        task_id, f"{YELLOW}[refresh]{RESET} {service}: not running\r\n"
                    )
            else:
                # Single-host: check assigned host first, then others
                found_host = None
                for host_name in [assigned_hosts[0]] + [h for h in config.hosts if h != assigned_hosts[0]]:
                    if await check_service_running(config, service, host_name):
                        found_host = host_name
                        break

                if found_host:
                    discovered[service] = found_host
                    await stream_to_task(
                        task_id, f"{GREEN}[refresh]{RESET} {service}: running on {found_host}\r\n"
                    )
                else:
                    await stream_to_task(
                        task_id, f"{YELLOW}[refresh]{RESET} {service}: not running\r\n"
                    )

        # Calculate changes
        added = [s for s in discovered if s not in current_state]
        removed = [s for s in current_state if s not in discovered]

        if added or removed:
            if added:
                await stream_to_task(
                    task_id, f"{GREEN}[refresh]{RESET} New: {', '.join(added)}\r\n"
                )
            if removed:
                await stream_to_task(
                    task_id, f"{YELLOW}[refresh]{RESET} Removed: {', '.join(removed)}\r\n"
                )
            save_state(config, discovered)
            await stream_to_task(
                task_id,
                f"{GREEN}[refresh]{RESET} State updated: {len(discovered)} services tracked\r\n",
            )
        else:
            await stream_to_task(
                task_id, f"{GREEN}[refresh]{RESET} State already in sync\r\n"
            )

        tasks[task_id]["status"] = "completed"

    except Exception as e:
        await stream_to_task(task_id, f"{RED}[refresh] Error: {e}{RESET}\r\n")
        tasks[task_id]["status"] = "failed"
