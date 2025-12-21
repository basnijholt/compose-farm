"""Action routes for stack operations."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

from compose_farm.web.deps import get_config
from compose_farm.web.streaming import run_cli_streaming, run_compose_streaming, tasks

router = APIRouter(tags=["actions"])

# Store task references to prevent garbage collection
_background_tasks: set[asyncio.Task[None]] = set()


def _start_task(coro_factory: Callable[[str], Coroutine[Any, Any, None]]) -> str:
    """Create a task, register it, and return the task_id."""
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "running", "output": []}

    task: asyncio.Task[None] = asyncio.create_task(coro_factory(task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return task_id


# Allowed stack commands
ALLOWED_COMMANDS = {"up", "down", "restart", "pull", "update", "logs", "stop"}

# Allowed service-level commands (no 'down' - use 'stop' for individual services)
ALLOWED_SERVICE_COMMANDS = {"logs", "pull", "restart", "up", "stop"}


@router.post("/stack/{name}/{command}")
async def stack_action(name: str, command: str) -> dict[str, Any]:
    """Run a compose command for a stack (up, down, restart, pull, update, logs, stop)."""
    if command not in ALLOWED_COMMANDS:
        raise HTTPException(status_code=404, detail=f"Unknown command '{command}'")

    config = get_config()
    if name not in config.stacks:
        raise HTTPException(status_code=404, detail=f"Stack '{name}' not found")

    task_id = _start_task(lambda tid: run_compose_streaming(config, name, command, tid))
    return {"task_id": task_id, "stack": name, "command": command}


@router.post("/stack/{name}/service/{service}/{command}")
async def service_action(name: str, service: str, command: str) -> dict[str, Any]:
    """Run a compose command for a specific service within a stack."""
    if command not in ALLOWED_SERVICE_COMMANDS:
        raise HTTPException(status_code=404, detail=f"Unknown command '{command}'")

    config = get_config()
    if name not in config.stacks:
        raise HTTPException(status_code=404, detail=f"Stack '{name}' not found")

    # Use --service flag to target specific service
    task_id = _start_task(
        lambda tid: run_compose_streaming(config, name, f"{command} --service {service}", tid)
    )
    return {"task_id": task_id, "stack": name, "service": service, "command": command}


@router.post("/apply")
async def apply_all() -> dict[str, Any]:
    """Run cf apply to reconcile all stacks."""
    config = get_config()
    task_id = _start_task(lambda tid: run_cli_streaming(config, ["apply"], tid))
    return {"task_id": task_id, "command": "apply"}


@router.post("/refresh")
async def refresh_state() -> dict[str, Any]:
    """Refresh state from running stacks."""
    config = get_config()
    task_id = _start_task(lambda tid: run_cli_streaming(config, ["refresh"], tid))
    return {"task_id": task_id, "command": "refresh"}


@router.post("/pull-all")
async def pull_all() -> dict[str, Any]:
    """Pull latest images for all stacks."""
    config = get_config()
    task_id = _start_task(lambda tid: run_cli_streaming(config, ["pull", "--all"], tid))
    return {"task_id": task_id, "command": "pull --all"}


@router.post("/update-all")
async def update_all() -> dict[str, Any]:
    """Update all stacks (pull + build + down + up)."""
    config = get_config()
    task_id = _start_task(lambda tid: run_cli_streaming(config, ["update", "--all"], tid))
    return {"task_id": task_id, "command": "update --all"}
