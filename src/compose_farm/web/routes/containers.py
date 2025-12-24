"""Container dashboard routes using Glances API."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from compose_farm.glances import ContainerStats, fetch_all_container_stats
from compose_farm.web.deps import get_config, get_templates

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


def _render_update_cell(image: str, tag: str) -> str:
    """Render update check cell with lazy loading via HTMX."""
    encoded_image = quote(image, safe="")
    encoded_tag = quote(tag, safe="")
    return f"""<td hx-get="/api/containers/check-update?image={encoded_image}&tag={encoded_tag}" hx-trigger="load" hx-swap="innerHTML"><span class="loading loading-spinner loading-xs"></span></td>"""


def _render_row(c: ContainerStats, idx: int) -> str:
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
    return f"""<tr>
<td class="text-xs opacity-50">{idx}</td>
<td data-sort="{stack.lower()}"><a href="/stack/{stack}" class="link link-hover link-primary" hx-boost="true">{stack}</a></td>
<td data-sort="{service.lower()}" class="text-xs opacity-70">{service}</td>
<td>{actions}</td>
<td data-sort="{c.host.lower()}"><span class="badge badge-outline badge-xs">{c.host}</span></td>
<td data-sort="{c.image.lower()}"><code class="text-xs bg-base-200 px-1 rounded">{image_name}:{tag}</code></td>
{update_cell}
<td data-sort="{c.status.lower()}"><span class="{_status_class(c.status)}">{c.status}</span></td>
<td data-sort="{uptime_sec}" class="text-xs">{c.uptime or "-"}</td>
<td data-sort="{cpu}"><div class="flex flex-col gap-0.5"><progress class="progress {cpu_class} w-12 h-2" value="{min(cpu, 100)}" max="100"></progress><span class="text-xs">{cpu:.0f}%</span></div></td>
<td data-sort="{c.memory_usage}"><div class="flex flex-col gap-0.5"><progress class="progress {mem_class} w-12 h-2" value="{min(mem, 100)}" max="100"></progress><span class="text-xs">{_format_bytes(c.memory_usage)}</span></div></td>
<td data-sort="{c.network_rx + c.network_tx}" class="text-xs font-mono">↓{_format_bytes(c.network_rx)} ↑{_format_bytes(c.network_tx)}</td>
</tr>"""


def _render_actions(stack: str) -> str:
    """Render actions dropdown for a container row."""
    return f"""<div class="dropdown dropdown-end">
<label tabindex="0" class="btn btn-circle btn-ghost btn-xs"><svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h.01M12 12h.01M19 12h.01M6 12a1 1 0 11-2 0 1 1 0 012 0zm7 0a1 1 0 11-2 0 1 1 0 012 0zm7 0a1 1 0 11-2 0 1 1 0 012 0z" /></svg></label>
<ul tabindex="0" class="dropdown-content menu menu-sm bg-base-200 rounded-box shadow-lg w-36 z-50 p-2">
<li><a hx-post="/api/stack/{stack}/restart" hx-swap="none" hx-confirm="Restart {stack}?"><svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>Restart</a></li>
<li><a hx-post="/api/stack/{stack}/pull" hx-swap="none"><svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>Pull</a></li>
<li><a hx-post="/api/stack/{stack}/update" hx-swap="none" hx-confirm="Update {stack}?"><svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>Update</a></li>
<li><a href="/stack/{stack}" hx-boost="true"><svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>Logs</a></li>
</ul>
</div>"""


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
            '<tr><td colspan="11" class="text-center text-error">Glances not configured</td></tr>'
        )

    containers = await fetch_all_container_stats(config)

    if not containers:
        return HTMLResponse(
            '<tr><td colspan="11" class="text-center py-4 opacity-60">No containers found</td></tr>'
        )

    rows = "\n".join(_render_row(c, i + 1) for i, c in enumerate(containers))
    return HTMLResponse(rows)


@router.get("/api/containers/check-update", response_class=HTMLResponse)
async def check_container_update_html(image: str, tag: str) -> HTMLResponse:
    """Check for updates and return HTML badge for HTMX.

    Returns a badge indicating update availability:
    - Green checkmark: up to date
    - Orange badge: updates available (with count)
    - Gray dash: check failed or unsupported
    """
    import httpx  # noqa: PLC0415

    from compose_farm.registry import check_image_updates  # noqa: PLC0415

    # Skip update checks for certain tags that don't make sense to check
    skip_tags = {"latest", "dev", "develop", "main", "master", "nightly"}
    if tag.lower() in skip_tags:
        return HTMLResponse('<span class="text-xs opacity-50">-</span>')

    full_image = f"{image}:{tag}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            result = await check_image_updates(full_image, client)

        if result.error:
            return HTMLResponse('<span class="text-xs opacity-50">-</span>')

        updates = result.available_updates
        if updates:
            count = len(updates)
            title = f"Newer: {', '.join(updates[:3])}" + ("..." if count > 3 else "")  # noqa: PLR2004
            return HTMLResponse(
                f'<span class="badge badge-warning badge-xs cursor-help" title="{title}">'
                f"{count} new</span>"
            )
        return HTMLResponse('<span class="text-success text-xs" title="Up to date">✓</span>')
    except Exception:
        return HTMLResponse('<span class="text-xs opacity-50">-</span>')
