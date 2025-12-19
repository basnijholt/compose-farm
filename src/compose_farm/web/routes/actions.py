"""Action routes for service operations."""

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


# Allowed service commands
ALLOWED_COMMANDS = {"up", "down", "restart", "pull", "update", "logs"}


@router.post("/service/{name}/{command}")
async def service_action(name: str, command: str) -> dict[str, Any]:
    """Run a compose command for a service (up, down, restart, pull, update, logs)."""
    if command not in ALLOWED_COMMANDS:
        raise HTTPException(status_code=404, detail=f"Unknown command '{command}'")

    config = get_config()
    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    task_id = _start_task(lambda tid: run_compose_streaming(config, name, command, tid))
    return {"task_id": task_id, "service": name, "command": command}


@router.post("/apply")
async def apply_all() -> dict[str, Any]:
    """Run cf apply to reconcile all services."""
    config = get_config()
    task_id = _start_task(lambda tid: run_cli_streaming(config, ["apply"], tid))
    return {"task_id": task_id, "command": "apply"}


@router.post("/refresh")
async def refresh_state() -> dict[str, Any]:
    """Refresh state from running services."""
    config = get_config()
    task_id = _start_task(lambda tid: run_cli_streaming(config, ["refresh"], tid))
    return {"task_id": task_id, "command": "refresh"}
