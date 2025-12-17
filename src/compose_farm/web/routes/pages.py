"""HTML page routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from compose_farm.web.app import get_config, get_templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Dashboard page."""
    config = get_config()
    templates = get_templates()

    # Get state
    from compose_farm.state import load_state

    state = load_state(config)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "config": config,
            "state": state,
            "services": sorted(config.services.keys()),
        },
    )


@router.get("/service/{name}", response_class=HTMLResponse)
async def service_detail(request: Request, name: str) -> HTMLResponse:
    """Service detail page."""
    config = get_config()
    templates = get_templates()

    # Get compose file content
    compose_path = config.get_compose_path(name)
    compose_content = ""
    if compose_path and compose_path.exists():
        compose_content = compose_path.read_text()

    # Get host info
    hosts = config.get_hosts(name)

    # Get state
    from compose_farm.state import get_service_host

    current_host = get_service_host(config, name)

    return templates.TemplateResponse(
        "service.html",
        {
            "request": request,
            "name": name,
            "hosts": hosts,
            "current_host": current_host,
            "compose_content": compose_content,
            "compose_path": str(compose_path) if compose_path else None,
            "services": sorted(config.services.keys()),
        },
    )


@router.get("/partials/sidebar", response_class=HTMLResponse)
async def sidebar_partial(request: Request) -> HTMLResponse:
    """Sidebar service list partial."""
    config = get_config()
    templates = get_templates()

    from compose_farm.state import load_state

    state = load_state(config)

    return templates.TemplateResponse(
        "partials/sidebar.html",
        {
            "request": request,
            "services": sorted(config.services.keys()),
            "state": state,
        },
    )
