"""HTML page routes."""

from __future__ import annotations

from typing import Annotated

import yaml
from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from compose_farm.executor import is_local
from compose_farm.paths import find_config_path
from compose_farm.state import (
    get_orphaned_services,
    get_service_host,
    get_services_needing_migration,
    get_services_not_in_state,
    group_running_services_by_host,
    load_state,
)
from compose_farm.web.deps import (
    extract_config_error,
    get_config,
    get_templates,
)

router = APIRouter()


@router.get("/console", response_class=HTMLResponse)
async def console(request: Request) -> HTMLResponse:
    """Console page with terminal and editor."""
    config = get_config()
    templates = get_templates()

    # Find local host and sort it first
    local_host = None
    for name, host in config.hosts.items():
        if is_local(host):
            local_host = name
            break

    # Sort hosts with local first
    hosts = sorted(config.hosts.keys())
    if local_host:
        hosts = [local_host] + [h for h in hosts if h != local_host]

    # Get config path for default editor file
    config_path = str(config.config_path) if config.config_path else ""

    return templates.TemplateResponse(
        "console.html",
        {
            "request": request,
            "hosts": hosts,
            "local_host": local_host,
            "config_path": config_path,
        },
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Dashboard page - combined view of all cluster info."""
    templates = get_templates()

    # Try to load config, handle errors gracefully
    config_error = None
    try:
        config = get_config()
    except (ValidationError, FileNotFoundError) as e:
        config_error = extract_config_error(e)

        # Read raw config content for the editor
        config_path = find_config_path()
        config_content = config_path.read_text() if config_path else ""

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "config_error": config_error,
                "hosts": {},
                "services": {},
                "config_content": config_content,
                "state_content": "",
                "running_count": 0,
                "stopped_count": 0,
                "orphaned": [],
                "migrations": [],
                "not_started": [],
                "services_by_host": {},
            },
        )

    # Get state
    deployed = load_state(config)

    # Stats
    running_count = len(deployed)
    stopped_count = len(config.services) - running_count

    # Pending operations
    orphaned = get_orphaned_services(config)
    migrations = get_services_needing_migration(config)
    not_started = get_services_not_in_state(config)

    # Group services by host (filter out hosts with no running services)
    services_by_host = group_running_services_by_host(deployed, config.hosts)

    # Config file content
    config_content = ""
    if config.config_path and config.config_path.exists():
        config_content = config.config_path.read_text()

    # State file content
    state_content = yaml.dump({"deployed": deployed}, default_flow_style=False, sort_keys=False)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "config_error": None,
            # Config data
            "hosts": config.hosts,
            "services": config.services,
            "config_content": config_content,
            # State data
            "state_content": state_content,
            # Stats
            "running_count": running_count,
            "stopped_count": stopped_count,
            # Pending operations
            "orphaned": orphaned,
            "migrations": migrations,
            "not_started": not_started,
            # Services by host
            "services_by_host": services_by_host,
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

    # Get .env file content
    env_content = ""
    env_path = None
    if compose_path:
        env_path = compose_path.parent / ".env"
        if env_path.exists():
            env_content = env_path.read_text()

    # Get host info
    hosts = config.get_hosts(name)

    # Get state
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
            "env_content": env_content,
            "env_path": str(env_path) if env_path else None,
        },
    )


@router.get("/partials/sidebar", response_class=HTMLResponse)
async def sidebar_partial(request: Request) -> HTMLResponse:
    """Sidebar service list partial."""
    config = get_config()
    templates = get_templates()

    state = load_state(config)

    # Build service -> host mapping (empty string for multi-host services)
    service_hosts = {
        svc: "" if host_val == "all" or isinstance(host_val, list) else host_val
        for svc, host_val in config.services.items()
    }

    return templates.TemplateResponse(
        "partials/sidebar.html",
        {
            "request": request,
            "services": sorted(config.services.keys()),
            "service_hosts": service_hosts,
            "hosts": sorted(config.hosts.keys()),
            "state": state,
        },
    )


@router.get("/partials/config-error", response_class=HTMLResponse)
async def config_error_partial(request: Request) -> HTMLResponse:
    """Config error banner partial."""
    templates = get_templates()
    try:
        get_config()
        return HTMLResponse("")  # No error
    except (ValidationError, FileNotFoundError) as e:
        error = extract_config_error(e)
        return templates.TemplateResponse(
            "partials/config_error.html", {"request": request, "config_error": error}
        )


@router.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request) -> HTMLResponse:
    """Stats cards partial (full wrapper)."""
    config = get_config()
    templates = get_templates()

    deployed = load_state(config)
    running_count = len(deployed)
    stopped_count = len(config.services) - running_count

    return templates.TemplateResponse(
        "partials/stats.html",
        {
            "request": request,
            "hosts": config.hosts,
            "services": config.services,
            "running_count": running_count,
            "stopped_count": stopped_count,
        },
    )


@router.get("/partials/stats-content", response_class=HTMLResponse)
async def stats_content_partial(request: Request) -> HTMLResponse:
    """Stats cards inner content (for innerHTML swap)."""
    config = get_config()
    templates = get_templates()

    deployed = load_state(config)
    running_count = len(deployed)
    stopped_count = len(config.services) - running_count

    return templates.TemplateResponse(
        "partials/stats_content.html",
        {
            "request": request,
            "hosts": config.hosts,
            "services": config.services,
            "running_count": running_count,
            "stopped_count": stopped_count,
        },
    )


@router.get("/partials/pending", response_class=HTMLResponse)
async def pending_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Pending operations partial (full wrapper)."""
    config = get_config()
    templates = get_templates()

    orphaned = get_orphaned_services(config)
    migrations = get_services_needing_migration(config)
    not_started = get_services_not_in_state(config)

    return templates.TemplateResponse(
        "partials/pending.html",
        {
            "request": request,
            "orphaned": orphaned,
            "migrations": migrations,
            "not_started": not_started,
            "expanded": expanded,
        },
    )


@router.get("/partials/pending-content", response_class=HTMLResponse)
async def pending_content_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Pending operations inner content (for innerHTML swap)."""
    config = get_config()
    templates = get_templates()

    orphaned = get_orphaned_services(config)
    migrations = get_services_needing_migration(config)
    not_started = get_services_not_in_state(config)

    return templates.TemplateResponse(
        "partials/pending_content.html",
        {
            "request": request,
            "orphaned": orphaned,
            "migrations": migrations,
            "not_started": not_started,
            "expanded": expanded,
        },
    )


@router.get("/partials/services-by-host", response_class=HTMLResponse)
async def services_by_host_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Services by host partial (full wrapper)."""
    config = get_config()
    templates = get_templates()

    deployed = load_state(config)
    services_by_host = group_running_services_by_host(deployed, config.hosts)

    return templates.TemplateResponse(
        "partials/services_by_host.html",
        {
            "request": request,
            "hosts": config.hosts,
            "services_by_host": services_by_host,
            "expanded": expanded,
        },
    )


@router.get("/partials/services-by-host-content", response_class=HTMLResponse)
async def services_by_host_content_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Services by host inner content (for innerHTML swap)."""
    config = get_config()
    templates = get_templates()

    deployed = load_state(config)
    services_by_host = group_running_services_by_host(deployed, config.hosts)

    return templates.TemplateResponse(
        "partials/services_by_host_content.html",
        {
            "request": request,
            "hosts": config.hosts,
            "services_by_host": services_by_host,
            "expanded": expanded,
        },
    )


@router.put("/htmx/save-config", response_class=HTMLResponse)
async def save_config_htmx(
    request: Request,
    content: Annotated[str, Body(media_type="text/plain")],
) -> HTMLResponse:
    """Save config and return HTML response with OOB swaps.

    This endpoint is designed for HTMX - it saves the config file and returns
    HTML that includes out-of-band swaps to refresh dashboard sections.
    """
    templates = get_templates()
    config_path = find_config_path()

    # Validate and save
    message = "Saved!"
    config_error = None
    try:
        yaml.safe_load(content)  # Validate YAML syntax
        if config_path:
            config_path.write_text(content)
    except yaml.YAMLError as e:
        message = f"Invalid YAML: {e}"
        config_error = message

    # Reload config to get fresh data for OOB elements
    try:
        config = get_config()
    except (ValidationError, FileNotFoundError) as e:
        config_error = extract_config_error(e)
        # Return error response without OOB (config is broken)
        return HTMLResponse(f"Error: {config_error}")

    # Load state and compute dashboard data
    deployed = load_state(config)
    running_count = len(deployed)
    stopped_count = len(config.services) - running_count
    orphaned = get_orphaned_services(config)
    migrations = get_services_needing_migration(config)
    not_started = get_services_not_in_state(config)
    services_by_host = group_running_services_by_host(deployed, config.hosts)

    # Build service -> host mapping for sidebar
    service_hosts = {
        svc: "" if host_val == "all" or isinstance(host_val, list) else host_val
        for svc, host_val in config.services.items()
    }

    return templates.TemplateResponse(
        "partials/save_response.html",
        {
            "request": request,
            "message": message,
            "config_error": config_error,
            # Stats
            "hosts": config.hosts,
            "services": config.services,
            "running_count": running_count,
            "stopped_count": stopped_count,
            # Pending
            "orphaned": orphaned,
            "migrations": migrations,
            "not_started": not_started,
            # Services by host
            "services_by_host": services_by_host,
            # Sidebar
            "service_hosts": service_hosts,
            "state": deployed,
        },
    )
