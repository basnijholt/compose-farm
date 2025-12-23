"""Container dashboard routes using Glances API."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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


def _status_class(status: str) -> str:
    """Get CSS class for status badge."""
    s = status.lower()
    if s == "running":
        return "badge badge-success badge-sm"
    if s == "exited":
        return "badge badge-error badge-sm"
    if s == "paused":
        return "badge badge-warning badge-sm"
    return "badge badge-ghost badge-sm"


def _progress_class(percent: float) -> str:
    """Get CSS class for progress bar color."""
    if percent > 80:  # noqa: PLR2004
        return "progress-error"
    if percent > 50:  # noqa: PLR2004
        return "progress-warning"
    return "progress-success"


def _render_row(c: ContainerStats) -> str:
    """Render a single container as an HTML table row."""
    image_name, tag = _parse_image(c.image)
    stack = c.stack if c.stack else _infer_stack_service(c.name)[0]
    service = c.service if c.service else _infer_stack_service(c.name)[1]

    cpu = c.cpu_percent
    mem = c.memory_percent
    cpu_class = _progress_class(cpu)
    mem_class = _progress_class(mem)

    return f"""<tr>
<td>{stack}</td>
<td class="text-xs opacity-70">{service}</td>
<td><span class="badge badge-outline badge-xs">{c.host}</span></td>
<td><code class="text-xs bg-base-200 px-1 rounded">{image_name}:{tag}</code></td>
<td><span class="{_status_class(c.status)}">{c.status}</span></td>
<td class="text-xs">{c.uptime or "-"}</td>
<td data-sort="{cpu:.1f}"><div class="flex flex-col gap-0.5"><progress class="progress {cpu_class} w-12 h-2" value="{min(cpu, 100)}" max="100"></progress><span class="text-xs">{cpu:.0f}%</span></div></td>
<td data-sort="{c.memory_usage}"><div class="flex flex-col gap-0.5"><progress class="progress {mem_class} w-12 h-2" value="{min(mem, 100)}" max="100"></progress><span class="text-xs">{_format_bytes(c.memory_usage)}</span></div></td>
<td data-sort="{c.network_rx + c.network_tx}" class="text-xs font-mono">↓{_format_bytes(c.network_rx)} ↑{_format_bytes(c.network_tx)}</td>
</tr>"""


def _parse_uptime_seconds(uptime: str) -> int:
    """Parse uptime string to seconds for sorting."""
    if not uptime:
        return 0
    uptime = uptime.lower().strip()
    # Handle "a/an" as 1
    uptime = uptime.replace("an ", "1 ").replace("a ", "1 ")

    total = 0
    multipliers = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
        "week": 604800,
        "month": 2592000,
        "year": 31536000,
    }
    for match in re.finditer(r"(\d+)\s*(\w+)", uptime):
        num = int(match.group(1))
        unit = match.group(2).rstrip("s")  # Remove plural 's'
        total += num * multipliers.get(unit, 0)
    return total


def _get_sort_key(sort: str) -> Any:
    """Get sort key function for a column name."""
    keys: dict[str, Any] = {
        "stack": lambda c: (c.stack or _infer_stack_service(c.name)[0]).lower(),
        "service": lambda c: (c.service or _infer_stack_service(c.name)[1]).lower(),
        "host": lambda c: c.host.lower(),
        "image": lambda c: c.image.lower(),
        "status": lambda c: c.status.lower(),
        "uptime": lambda c: _parse_uptime_seconds(c.uptime),
        "cpu": lambda c: c.cpu_percent,
        "mem": lambda c: c.memory_usage,
        "net": lambda c: c.network_rx + c.network_tx,
    }
    return keys.get(sort, keys["cpu"])


@router.get("/api/containers/rows", response_class=HTMLResponse)
async def get_containers_rows(sort: str = "cpu", asc: bool = False) -> HTMLResponse:
    """Get container table rows as HTML for HTMX."""
    config = get_config()

    if not config.glances_stack:
        return HTMLResponse(
            '<tr><td colspan="9" class="text-center text-error">Glances not configured</td></tr>'
        )

    containers = await fetch_all_container_stats(config)

    if not containers:
        return HTMLResponse(
            '<tr><td colspan="9" class="text-center py-4 opacity-60">No containers found</td></tr>'
        )

    # Sort by requested column
    containers.sort(key=_get_sort_key(sort), reverse=not asc)

    rows = "\n".join(_render_row(c) for c in containers)
    return HTMLResponse(rows)


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
