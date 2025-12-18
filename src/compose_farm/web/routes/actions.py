"""Action routes for service operations."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from compose_farm.web.app import get_config
from compose_farm.web.streaming import run_compose_streaming, run_refresh_streaming, tasks

router = APIRouter(tags=["actions"])

# Store task references to prevent garbage collection
_background_tasks: set[asyncio.Task[None]] = set()


async def _run_service_action(
    name: str,
    command: str,
) -> dict[str, Any]:
    """Run a compose command for a service."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "running", "output": []}

    # Use create_task for true concurrent execution (doesn't block event loop)
    task = asyncio.create_task(run_compose_streaming(config, name, command, task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"task_id": task_id, "service": name, "command": command}


@router.post("/service/{name}/up")  # type: ignore[misc]
async def up_service(name: str) -> dict[str, Any]:
    """Start a service."""
    return await _run_service_action(name, "up")


@router.post("/service/{name}/down")  # type: ignore[misc]
async def down_service(name: str) -> dict[str, Any]:
    """Stop a service."""
    return await _run_service_action(name, "down")


@router.post("/service/{name}/restart")  # type: ignore[misc]
async def restart_service(name: str) -> dict[str, Any]:
    """Restart a service (down + up)."""
    return await _run_service_action(name, "restart")


@router.post("/service/{name}/pull")  # type: ignore[misc]
async def pull_service(name: str) -> dict[str, Any]:
    """Pull latest images for a service."""
    return await _run_service_action(name, "pull")


@router.post("/service/{name}/logs")  # type: ignore[misc]
async def logs_service(name: str) -> dict[str, Any]:
    """Show logs for a service."""
    return await _run_service_action(name, "logs")


@router.post("/apply")  # type: ignore[misc]
async def apply_all() -> dict[str, Any]:
    """Run cf apply to reconcile all services."""
    from compose_farm.web.streaming import run_apply_streaming

    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "running", "output": []}

    config = get_config()
    task = asyncio.create_task(run_apply_streaming(config, task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"task_id": task_id, "command": "apply"}


@router.post("/refresh")  # type: ignore[misc]
async def refresh_state() -> dict[str, Any]:
    """Refresh state from running services."""
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "running", "output": []}

    config = get_config()
    task = asyncio.create_task(run_refresh_streaming(config, task_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"task_id": task_id, "command": "refresh"}
