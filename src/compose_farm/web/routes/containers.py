"""Container dashboard routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from compose_farm.executor import run_command, run_compose
from compose_farm.logs import _extract_image_fields, _parse_images_output
from compose_farm.state import load_state
from compose_farm.web.deps import get_config, get_templates

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine

    from compose_farm.config import Config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["containers"])


@dataclass
class ContainerInfo:
    """Container information for the dashboard table."""

    stack: str
    service: str
    container: str
    host: str
    image: str
    tag: str
    digest: str
    status: str
    uptime: str
    restarts: int
    cpu_percent: float
    memory_usage: str
    memory_percent: float
    net_io: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON response."""
        return {
            "stack": self.stack,
            "service": self.service,
            "container": self.container,
            "host": self.host,
            "image": self.image,
            "tag": self.tag,
            "digest": self.digest,
            "status": self.status,
            "uptime": self.uptime,
            "restarts": self.restarts,
            "cpu_percent": self.cpu_percent,
            "memory_usage": self.memory_usage,
            "memory_percent": self.memory_percent,
            "net_io": self.net_io,
        }


async def _get_stack_containers(
    config: Config,
    stack: str,
    host_name: str,  # noqa: ARG001
) -> list[dict[str, Any]]:
    """Get container info for a stack using docker compose ps."""
    result = await run_compose(config, stack, "ps -a --format json", stream=False, prefix="")
    if not result.success:
        logger.warning("Failed to get containers for %s: %s", stack, result.stderr)
        return []

    containers = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            data = json.loads(line)
            containers.append(
                {
                    "name": data.get("Name", ""),
                    "service": data.get("Service", ""),
                    "status": data.get("State", "unknown"),
                    "health": data.get("Health", ""),
                    "running_for": data.get("RunningFor", ""),
                    "exit_code": data.get("ExitCode", 0),
                }
            )
    return containers


async def _get_stack_images(config: Config, stack: str) -> dict[str, tuple[str, str, str]]:
    """Get image info for a stack. Returns dict of service -> (image, tag, digest)."""
    result = await run_compose(config, stack, "images --format json", stream=False, prefix="")
    if not result.success:
        return {}

    records = _parse_images_output(result.stdout)
    images: dict[str, tuple[str, str, str]] = {}

    for record in records:
        # Try Service first, then ContainerName (images output uses ContainerName)
        service = (
            record.get("Service") or record.get("ContainerName", "") or record.get("Container", "")
        )
        image, digest = _extract_image_fields(record)

        # Split image:tag
        if ":" in image:
            img_name, tag = image.rsplit(":", 1)
        else:
            img_name, tag = image, "latest"

        images[service] = (img_name, tag, digest)

    return images


async def _get_container_stats(
    config: Config, host_name: str, container_names: list[str]
) -> dict[str, dict[str, Any]]:
    """Get stats for containers on a host using docker stats."""
    if not container_names:
        return {}

    host = config.hosts[host_name]
    # Use docker stats with --no-stream for a single snapshot
    names_arg = " ".join(container_names)
    cmd = f"docker stats --no-stream --format json {names_arg}"

    result = await run_command(host, cmd, "stats", stream=False, prefix="")
    if not result.success:
        logger.warning("Failed to get stats for %s: %s", host_name, result.stderr)
        return {}

    stats: dict[str, dict[str, Any]] = {}
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            data = json.loads(line)
            name = data.get("Name", "")
            if name:
                stats[name] = {
                    "cpu_percent": _parse_percent(data.get("CPUPerc", "0%")),
                    "memory_usage": data.get("MemUsage", ""),
                    "memory_percent": _parse_percent(data.get("MemPerc", "0%")),
                    "net_io": data.get("NetIO", ""),
                }
    return stats


async def _get_container_restarts(
    config: Config, host_name: str, container_names: list[str]
) -> dict[str, int]:
    """Get restart counts for containers."""
    if not container_names:
        return {}

    host = config.hosts[host_name]
    # Use docker inspect to get restart counts
    names_arg = " ".join(container_names)
    cmd = f"docker inspect --format '{{{{.Name}}}}:{{{{.RestartCount}}}}' {names_arg}"

    result = await run_command(host, cmd, "inspect", stream=False, prefix="")
    if not result.success:
        return {}

    restarts: dict[str, int] = {}
    for line in result.stdout.strip().split("\n"):
        if ":" in line:
            name, count = line.rsplit(":", 1)
            # Remove leading slash from container name
            name = name.lstrip("/")
            with contextlib.suppress(ValueError):
                restarts[name] = int(count)
    return restarts


def _parse_percent(value: str) -> float:
    """Parse a percentage string like '12.5%' to float."""
    try:
        return float(value.rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


async def _collect_all_containers(config: Config) -> list[ContainerInfo]:
    """Collect container info for all running stacks."""
    state = load_state(config)
    if not state:
        return []

    all_containers: list[ContainerInfo] = []
    tasks: list[Coroutine[None, None, list[ContainerInfo]]] = []

    # Collect containers from each stack
    for stack, hosts in state.items():
        if stack not in config.stacks:
            continue  # Skip orphaned stacks

        host_list = hosts if isinstance(hosts, list) else [hosts]
        tasks.extend(_collect_stack_containers(config, stack, host_name) for host_name in host_list)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            all_containers.extend(result)
        elif isinstance(result, Exception):
            logger.warning("Error collecting containers: %s", result)

    return all_containers


async def _collect_stack_containers(
    config: Config, stack: str, host_name: str
) -> list[ContainerInfo]:
    """Collect container info for a single stack on a host."""
    # Get containers, images, and stats in parallel
    containers_task = _get_stack_containers(config, stack, host_name)
    images_task = _get_stack_images(config, stack)

    containers, images = await asyncio.gather(containers_task, images_task)

    if not containers:
        return []

    # Get container names for stats query
    container_names = [c["name"] for c in containers if c["name"]]

    # Get stats and restarts in parallel
    stats_task = _get_container_stats(config, host_name, container_names)
    restarts_task = _get_container_restarts(config, host_name, container_names)

    stats, restarts = await asyncio.gather(stats_task, restarts_task)

    result: list[ContainerInfo] = []
    for c in containers:
        name = c["name"]
        service = c["service"]
        # Try service name first, then container name (images output uses ContainerName)
        img_name, tag, digest = images.get(service) or images.get(name, ("", "", ""))
        container_stats = stats.get(name, {})

        result.append(
            ContainerInfo(
                stack=stack,
                service=service,
                container=name,
                host=host_name,
                image=img_name,
                tag=tag,
                digest=digest,
                status=c["status"],
                uptime=c["running_for"],
                restarts=restarts.get(name, 0),
                cpu_percent=container_stats.get("cpu_percent", 0.0),
                memory_usage=container_stats.get("memory_usage", ""),
                memory_percent=container_stats.get("memory_percent", 0.0),
                net_io=container_stats.get("net_io", ""),
            )
        )

    return result


@router.get("/containers", response_class=HTMLResponse)
async def containers_page(request: Request) -> HTMLResponse:
    """Container dashboard page."""
    templates = get_templates()

    return templates.TemplateResponse(
        "containers.html",
        {"request": request},
    )


@router.get("/api/containers", response_class=JSONResponse)
async def get_containers_data() -> JSONResponse:
    """Get all container data as JSON for the table."""
    config = get_config()
    containers = await _collect_all_containers(config)

    return JSONResponse(
        content={"data": [c.to_dict() for c in containers]},
    )


@router.get("/api/containers/stream")
async def stream_containers_data() -> StreamingResponse:
    """Stream container data as SSE events as each stack completes."""
    config = get_config()

    async def generate_events() -> AsyncGenerator[str, None]:
        """Generate SSE events for each stack's containers."""
        state = load_state(config)
        if not state:
            yield "event: done\ndata: {}\n\n"
            return

        # Create tasks for each stack/host combination
        tasks: list[Coroutine[None, None, list[ContainerInfo]]] = []
        for stack_name, hosts in state.items():
            if stack_name not in config.stacks:
                continue
            host_list = hosts if isinstance(hosts, list) else [hosts]
            tasks.extend(
                _collect_stack_containers(config, stack_name, host_name) for host_name in host_list
            )

        # Yield results as each task completes
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                if result:
                    # Send containers as SSE data event
                    data = json.dumps([c.to_dict() for c in result])
                    yield f"data: {data}\n\n"
            except Exception as e:
                logger.warning("Error collecting containers: %s", e)

        # Signal completion
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get("/api/containers/{stack}/{service}/tags", response_class=HTMLResponse)
async def check_container_tags(stack: str, service: str) -> HTMLResponse:
    """Check registry for available tags for a container's image."""
    import httpx  # noqa: PLC0415 - lazy import

    from compose_farm.registry import check_image_tags  # noqa: PLC0415

    config = get_config()

    # Get image info for this service
    images = await _get_stack_images(config, stack)
    if service not in images:
        return HTMLResponse('<span class="badge badge-error whitespace-nowrap">Not found</span>')

    img_name, tag, digest = images[service]
    image_str = f"{img_name}:{tag}" if tag else img_name

    # Check tags from registry
    async with httpx.AsyncClient(timeout=30.0) as client:
        result = await check_image_tags(image_str, digest, client)

    if result.error:
        return HTMLResponse(
            f'<span class="badge badge-error whitespace-nowrap" title="{result.error}">Error</span>'
        )

    # Format result
    max_display = 5  # Max tags to show in tooltip
    if result.available_updates:
        updates_list = ", ".join(result.available_updates[:max_display])
        more = (
            f" +{len(result.available_updates) - max_display} more"
            if len(result.available_updates) > max_display
            else ""
        )
        return HTMLResponse(
            f'<span class="badge badge-warning whitespace-nowrap cursor-help" '
            f'title="Updates: {updates_list}{more}">'
            f"{len(result.available_updates)} updates</span>"
        )

    equiv = [t for t in result.equivalent_tags if t != tag]
    if equiv:
        equiv_str = ", ".join(equiv[:3])
        return HTMLResponse(
            f'<span class="badge badge-success whitespace-nowrap cursor-help" '
            f'title="Also known as: {equiv_str}">Up to date</span>'
        )

    return HTMLResponse('<span class="badge badge-success whitespace-nowrap">Up to date</span>')
