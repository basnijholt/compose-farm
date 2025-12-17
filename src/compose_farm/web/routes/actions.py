"""Action routes for service operations."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from compose_farm.web.app import get_config
from compose_farm.web.streaming import run_compose_streaming, tasks

router = APIRouter(tags=["actions"])


async def _run_service_action(
    name: str,
    command: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Run a compose command for a service."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "running", "output": []}

    background_tasks.add_task(run_compose_streaming, config, name, command, task_id)

    return {"task_id": task_id, "service": name, "command": command}


@router.post("/service/{name}/up")
async def up_service(name: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Start a service."""
    return await _run_service_action(name, "up -d", background_tasks)


@router.post("/service/{name}/down")
async def down_service(name: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Stop a service."""
    return await _run_service_action(name, "down", background_tasks)


@router.post("/service/{name}/restart")
async def restart_service(name: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Restart a service (down + up)."""
    return await _run_service_action(name, "down && docker compose up -d", background_tasks)


@router.post("/service/{name}/pull")
async def pull_service(name: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Pull latest images for a service."""
    return await _run_service_action(name, "pull", background_tasks)


@router.post("/apply")
async def apply_all(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Run cf apply to reconcile all services."""
    from compose_farm.web.streaming import run_apply_streaming

    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "running", "output": []}

    config = get_config()
    background_tasks.add_task(run_apply_streaming, config, task_id)

    return {"task_id": task_id, "command": "apply"}
