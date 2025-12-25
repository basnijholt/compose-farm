"""Container dashboard routes using Glances API."""

from __future__ import annotations

import re
from urllib.parse import quote

import humanize
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from compose_farm.executor import TTLCache
from compose_farm.glances import ContainerStats, fetch_all_container_stats
from compose_farm.web.deps import get_config, get_templates

router = APIRouter(tags=["containers"])

# Cache registry update checks for 5 minutes (300 seconds)
# Registry calls are slow and often rate-limited
_update_check_cache = TTLCache(ttl_seconds=300.0)

# Minimum parts needed to infer stack/service from container name
MIN_NAME_PARTS = 2

# HTML for "no update info" dash
_DASH_HTML = '<span class="text-xs opacity-50">-</span>'


def _format_bytes(bytes_val: int) -> str:
    """Format bytes to human readable string."""
    return humanize.naturalsize(bytes_val, binary=True, format="%.1f")


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


@router.get("/live-stats", response_class=HTMLResponse)
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
            "hosts": sorted(config.hosts.keys()) if glances_enabled else [],
        },
    )


_STATUS_CLASSES = {
    "running": "badge badge-success badge-sm",
    "exited": "badge badge-error badge-sm",
    "paused": "badge badge-warning badge-sm",
}


def _status_class(status: str) -> str:
    """Get CSS class for status badge."""
    return _STATUS_CLASSES.get(status.lower(), "badge badge-ghost badge-sm")


def _progress_class(percent: float) -> str:
    """Get CSS class for progress bar color."""
    if percent > 80:  # noqa: PLR2004
        return "bg-error"
    if percent > 50:  # noqa: PLR2004
        return "bg-warning"
    return "bg-success"


def _render_update_cell(image: str, tag: str) -> str:
    """Render update check cell with lazy loading via HTMX."""
    encoded_image = quote(image, safe="")
    encoded_tag = quote(tag, safe="")
    return f"""<td hx-get="/api/containers/check-update?image={encoded_image}&tag={encoded_tag}" hx-trigger="load" hx-swap="innerHTML"><span class="loading loading-spinner loading-xs"></span></td>"""


def _render_row(c: ContainerStats, idx: int | str) -> str:
    """Render a single container as an HTML table row."""
    image_name, tag = _parse_image(c.image)
    stack = c.stack if c.stack else _infer_stack_service(c.name)[0]
    service = c.service if c.service else _infer_stack_service(c.name)[1]

    cpu = c.cpu_percent
    mem = c.memory_percent
    cpu_class = _progress_class(cpu)
    mem_class = _progress_class(mem)

    uptime_sec = _parse_uptime_seconds(c.uptime)
    actions = _render_actions(stack)
    update_cell = _render_update_cell(image_name, tag)
    # Render as single line to avoid whitespace nodes in DOM
    row_id = f"c-{c.host}-{c.name}"
    return (
        f'<tr id="{row_id}" data-host="{c.host}"><td class="text-xs opacity-50">{idx}</td>'
        f'<td data-sort="{stack.lower()}"><a href="/stack/{stack}" class="link link-hover link-primary" hx-boost="true">{stack}</a></td>'
        f'<td data-sort="{service.lower()}" class="text-xs opacity-70">{service}</td>'
        f"<td>{actions}</td>"
        f'<td data-sort="{c.host.lower()}"><span class="badge badge-outline badge-xs">{c.host}</span></td>'
        f'<td data-sort="{c.image.lower()}"><code class="text-xs bg-base-200 px-1 rounded">{image_name}:{tag}</code></td>'
        f"{update_cell}"
        f'<td data-sort="{c.status.lower()}"><span class="{_status_class(c.status)}">{c.status}</span></td>'
        f'<td data-sort="{uptime_sec}" class="text-xs">{c.uptime or "-"}</td>'
        f'<td data-sort="{cpu}"><div class="flex flex-col gap-0.5"><div class="w-12 h-2 bg-base-300 rounded-full overflow-hidden"><div class="h-full {cpu_class}" style="width: {min(cpu, 100)}%"></div></div><span class="text-xs">{cpu:.0f}%</span></div></td>'
        f'<td data-sort="{c.memory_usage}"><div class="flex flex-col gap-0.5"><div class="w-12 h-2 bg-base-300 rounded-full overflow-hidden"><div class="h-full {mem_class}" style="width: {min(mem, 100)}%"></div></div><span class="text-xs">{_format_bytes(c.memory_usage)}</span></div></td>'
        f'<td data-sort="{c.network_rx + c.network_tx}" class="text-xs font-mono">↓{_format_bytes(c.network_rx)} ↑{_format_bytes(c.network_tx)}</td>'
        "</tr>"
    )


def _render_actions(stack: str) -> str:
    """Render actions dropdown for a container row."""
    return f"""<button class="btn btn-circle btn-ghost btn-xs" onclick="openActionMenu(event, '{stack}')" aria-label="Actions for {stack}">
<svg class="h-4 w-4"><use href="#icon-menu" /></svg>
</button>"""


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


@router.get("/api/containers/rows", response_class=HTMLResponse)
async def get_containers_rows() -> HTMLResponse:
    """Get container table rows as HTML for HTMX.

    Each cell has data-sort attribute for instant client-side sorting.
    """
    config = get_config()

    if not config.glances_stack:
        return HTMLResponse(
            '<tr><td colspan="12" class="text-center text-error">Glances not configured</td></tr>'
        )

    containers = await fetch_all_container_stats(config)

    if not containers:
        return HTMLResponse(
            '<tr><td colspan="12" class="text-center py-4 opacity-60">No containers found</td></tr>'
        )

    rows = "\n".join(_render_row(c, i + 1) for i, c in enumerate(containers))
    return HTMLResponse(rows)


@router.get("/api/containers/rows/{host_name}", response_class=HTMLResponse)
async def get_containers_rows_by_host(host_name: str) -> HTMLResponse:
    """Get container rows for a specific host.

    Returns immediately with Glances data. Stack/service are inferred from
    container names for instant display (no SSH wait).
    """
    import logging  # noqa: PLC0415
    import time  # noqa: PLC0415

    from compose_farm.glances import fetch_container_stats  # noqa: PLC0415

    logger = logging.getLogger(__name__)
    config = get_config()

    if host_name not in config.hosts:
        return HTMLResponse("")

    host = config.hosts[host_name]

    t0 = time.monotonic()
    containers, error = await fetch_container_stats(host_name, host.address)
    t1 = time.monotonic()
    fetch_ms = (t1 - t0) * 1000

    if containers is None:
        logger.error(
            "Failed to fetch stats for %s in %.1fms: %s",
            host_name,
            fetch_ms,
            error,
        )
        return HTMLResponse(
            f'<tr class="text-error"><td colspan="12" class="text-center py-2">Error: {error}</td></tr>'
        )

    if not containers:
        return HTMLResponse("")  # No rows for this host

    # Infer stack/service from container name (fast, no SSH)
    for c in containers:
        c.stack, c.service = _infer_stack_service(c.name)

    # Use placeholder index (will be renumbered by JS after all hosts load)
    rows = "\n".join(_render_row(c, "-") for c in containers)
    t2 = time.monotonic()
    render_ms = (t2 - t1) * 1000

    logger.info(
        "Loaded %d rows for %s in %.1fms (fetch) + %.1fms (render)",
        len(containers),
        host_name,
        fetch_ms,
        render_ms,
    )
    return HTMLResponse(rows)


@router.get("/api/containers/check-update", response_class=HTMLResponse)
async def check_container_update_html(image: str, tag: str) -> HTMLResponse:
    """Check for updates and return HTML badge for HTMX.

    Returns a badge indicating update availability:
    - Green checkmark: up to date
    - Orange badge: updates available (with count)
    - Gray dash: check failed or unsupported

    Results are cached for 5 minutes to reduce registry API calls.
    """
    import httpx  # noqa: PLC0415

    from compose_farm.registry import check_image_updates  # noqa: PLC0415

    # Skip update checks for certain tags that don't make sense to check
    skip_tags = {"latest", "dev", "develop", "main", "master", "nightly"}
    if tag.lower() in skip_tags:
        return HTMLResponse(_DASH_HTML)

    full_image = f"{image}:{tag}"

    # Check cache first
    cached_html: str | None = _update_check_cache.get(full_image)
    if cached_html is not None:
        return HTMLResponse(cached_html)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            result = await check_image_updates(full_image, client)

        if result.error:
            html = _DASH_HTML
        elif result.available_updates:
            updates = result.available_updates
            count = len(updates)
            title = f"Newer: {', '.join(updates[:3])}" + ("..." if count > 3 else "")  # noqa: PLR2004
            html = (
                f'<span class="badge badge-warning badge-xs cursor-help" title="{title}">'
                f"{count} new</span>"
            )
        else:
            html = '<span class="text-success text-xs" title="Up to date">✓</span>'

        # Cache the result
        _update_check_cache.set(full_image, html)
        return HTMLResponse(html)
    except Exception:
        # Cache errors for 1 minute to prevent hammering registry on failures
        # (e.g. auth errors, rate limits, non-existent tags)
        # Use a distinctive error marker if needed, or just the dash
        _update_check_cache.set(full_image, _DASH_HTML, ttl_seconds=60.0)
        return HTMLResponse(_DASH_HTML)
