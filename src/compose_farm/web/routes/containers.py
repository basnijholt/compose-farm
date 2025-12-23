"""Container dashboard routes using Glances API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from compose_farm.glances import ContainerStats, fetch_all_container_stats
from compose_farm.web.deps import get_config, get_templates

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["containers"])

# Byte size constants
KB = 1024
MB = KB * 1024
GB = MB * 1024

# Minimum parts needed to infer stack/service from container name
MIN_NAME_PARTS = 2


def _format_bytes(bytes_val: int) -> str:
    """Format bytes to human readable string."""
    if bytes_val < KB:
        return f"{bytes_val}B"
    if bytes_val < MB:
        return f"{bytes_val / KB:.1f}KB"
    if bytes_val < GB:
        return f"{bytes_val / MB:.1f}MB"
    return f"{bytes_val / GB:.1f}GB"


def _parse_image(image: str) -> tuple[str, str]:
    """Parse image string into (name, tag)."""
    # Handle registry prefix (e.g., ghcr.io/user/repo:tag)
    if ":" in image:
        # Find last colon that's not part of port
        parts = image.rsplit(":", 1)
        if "/" in parts[-1]:
            # The "tag" contains a slash, so it's probably a port
            return image, "latest"
        return parts[0], parts[1]
    return image, "latest"


def _infer_stack_service(name: str) -> tuple[str, str]:
    """Fallback: infer stack and service from container name.

    Used when compose labels are not available.
    Docker Compose naming conventions:
    - Default: {project}_{service}_{instance} or {project}-{service}-{instance}
    - Custom: {container_name} from compose file
    """
    # Try underscore separator first (older compose)
    if "_" in name:
        parts = name.split("_")
        if len(parts) >= MIN_NAME_PARTS:
            return parts[0], parts[1]
    # Try hyphen separator (newer compose)
    if "-" in name:
        parts = name.split("-")
        if len(parts) >= MIN_NAME_PARTS:
            return parts[0], "-".join(parts[1:-1]) if len(parts) > MIN_NAME_PARTS else parts[1]
    # Fallback: use name as both stack and service
    return name, name


def container_to_dict(c: ContainerStats) -> dict[str, Any]:
    """Convert ContainerStats to dictionary for JSON response."""
    image_name, tag = _parse_image(c.image)
    # Use compose labels if available, otherwise fall back to heuristic
    stack = c.stack if c.stack else _infer_stack_service(c.name)[0]
    service = c.service if c.service else _infer_stack_service(c.name)[1]

    return {
        "name": c.name,
        "stack": stack,
        "service": service,
        "host": c.host,
        "image": image_name,
        "tag": tag,
        "status": c.status,
        "uptime": c.uptime,
        "cpu_percent": round(c.cpu_percent, 1),
        "memory_usage": _format_bytes(c.memory_usage),
        "memory_percent": round(c.memory_percent, 1),
        "memory_mb": round(c.memory_usage_mb, 0),
        "network_rx": _format_bytes(c.network_rx),
        "network_tx": _format_bytes(c.network_tx),
        "net_io": f"↓{_format_bytes(c.network_rx)} ↑{_format_bytes(c.network_tx)}",
        "ports": c.ports,
        "engine": c.engine,
    }


@router.get("/containers", response_class=HTMLResponse)
async def containers_page(request: Request) -> HTMLResponse:
    """Container dashboard page."""
    config = get_config()
    templates = get_templates()

    # Check if Glances is configured
    glances_enabled = config.glances_stack is not None

    return templates.TemplateResponse(
        "containers.html",
        {
            "request": request,
            "glances_enabled": glances_enabled,
        },
    )


@router.get("/api/containers/list", response_class=JSONResponse)
async def get_containers_data() -> JSONResponse:
    """Get all container data from Glances as JSON."""
    config = get_config()

    if not config.glances_stack:
        return JSONResponse(
            content={"error": "Glances not configured", "data": []},
            status_code=200,
        )

    containers = await fetch_all_container_stats(config)

    # Sort by CPU usage descending
    containers.sort(key=lambda c: c.cpu_percent, reverse=True)

    return JSONResponse(
        content={
            "data": [container_to_dict(c) for c in containers],
            "count": len(containers),
        },
    )


@router.get("/api/containers/stream")
async def stream_containers_data() -> StreamingResponse:
    """Stream container data from each host as it arrives (SSE)."""
    from compose_farm.glances import (  # noqa: PLC0415
        _fetch_compose_labels,
        fetch_container_stats,
    )

    config = get_config()

    if not config.glances_stack:

        async def error_stream() -> AsyncGenerator[str, None]:
            yield f"data: {json.dumps({'error': 'Glances not configured'})}\n\n"

        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def generate() -> AsyncGenerator[str, None]:
        # Create tasks for each host
        async def fetch_host(host_name: str, host_address: str) -> list[dict[str, Any]]:
            stats_task = fetch_container_stats(host_name, host_address)
            labels_task = _fetch_compose_labels(config, host_name)
            containers, labels = await asyncio.gather(stats_task, labels_task)

            result = []
            for c in containers:
                stack, service = labels.get(c.name, ("", ""))
                enriched = ContainerStats(
                    name=c.name,
                    host=c.host,
                    status=c.status,
                    image=c.image,
                    cpu_percent=c.cpu_percent,
                    memory_usage=c.memory_usage,
                    memory_limit=c.memory_limit,
                    memory_percent=c.memory_percent,
                    network_rx=c.network_rx,
                    network_tx=c.network_tx,
                    uptime=c.uptime,
                    ports=c.ports,
                    engine=c.engine,
                    stack=stack,
                    service=service,
                )
                result.append(container_to_dict(enriched))
            return result

        # Create all tasks
        tasks = {
            asyncio.create_task(fetch_host(name, host.address)): name
            for name, host in config.hosts.items()
        }

        # Yield results as they complete
        for coro in asyncio.as_completed(tasks):
            try:
                containers = await coro
                if containers:
                    yield f"data: {json.dumps({'host_data': containers})}\n\n"
            except Exception:
                logger.debug("Failed to fetch containers from host", exc_info=True)

        # Signal completion
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/api/containers/check-updates", response_class=JSONResponse)
async def check_container_updates(image: str, tag: str) -> JSONResponse:
    """Check for updates for a specific image.

    Args:
        image: Image name (e.g., "nginx", "ghcr.io/user/repo")
        tag: Current tag (e.g., "latest", "1.25.0")

    Returns:
        JSON with available_updates list

    """
    import httpx  # noqa: PLC0415

    from compose_farm.registry import check_image_tags  # noqa: PLC0415

    full_image = f"{image}:{tag}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            result = await check_image_tags(full_image, "", client, fetch_digests=False)

        return JSONResponse(
            content={
                "image": image,
                "tag": tag,
                "available_updates": result.available_updates[:5],  # Top 5 updates
                "error": result.error,
            },
        )
    except Exception as e:
        return JSONResponse(
            content={
                "image": image,
                "tag": tag,
                "available_updates": [],
                "error": str(e),
            },
        )
