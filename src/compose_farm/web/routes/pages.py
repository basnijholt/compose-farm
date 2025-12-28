"""HTML page routes."""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from compose_farm.compose import extract_services, get_container_name, parse_compose_data
from compose_farm.paths import find_config_path
from compose_farm.state import (
    get_orphaned_stacks,
    get_stack_host,
    get_stacks_needing_migration,
    get_stacks_not_in_state,
    group_running_stacks_by_host,
    load_state,
)
from compose_farm.traefik import extract_website_urls
from compose_farm.web.deps import (
    extract_config_error,
    get_config,
    get_local_host,
    get_templates,
)

router = APIRouter()


@router.get("/console", response_class=HTMLResponse)
async def console(request: Request) -> HTMLResponse:
    """Console page with terminal and editor."""
    config = get_config()
    templates = get_templates()

    # Sort hosts with local first
    local_host = get_local_host(config)
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
                "stacks": {},
                "config_content": config_content,
                "state_content": "",
                "running_count": 0,
                "stopped_count": 0,
                "orphaned": [],
                "migrations": [],
                "not_started": [],
                "stacks_by_host": {},
            },
        )

    # Get state
    deployed = load_state(config)

    # Stats (only count stacks that are both in config AND deployed)
    running_count = sum(1 for stack in deployed if stack in config.stacks)
    stopped_count = len(config.stacks) - running_count

    # Pending operations
    orphaned = get_orphaned_stacks(config)
    migrations = get_stacks_needing_migration(config)
    not_started = get_stacks_not_in_state(config)

    # Group stacks by host (filter out hosts with no running stacks)
    stacks_by_host = group_running_stacks_by_host(deployed, config.hosts)

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
            "stacks": config.stacks,
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
            # Stacks by host
            "stacks_by_host": stacks_by_host,
        },
    )


@router.get("/stack/{name}", response_class=HTMLResponse)
async def stack_detail(request: Request, name: str) -> HTMLResponse:
    """Stack detail page."""
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
    current_host = get_stack_host(config, name)

    # Get service names and container info from compose file
    services: list[str] = []
    containers: dict[str, dict[str, str]] = {}
    shell_host = current_host[0] if isinstance(current_host, list) else current_host
    if compose_content:
        compose_data = parse_compose_data(compose_content)
        raw_services = extract_services(compose_data)
        if raw_services:
            services = list(raw_services.keys())
            # Build container info for shell access (only if stack is running)
            if shell_host:
                project_name = compose_path.parent.name if compose_path else name
                containers = {
                    svc: {
                        "container": get_container_name(svc, svc_def, project_name),
                        "host": shell_host,
                    }
                    for svc, svc_def in raw_services.items()
                }

    # Extract website URLs from Traefik labels
    website_urls = extract_website_urls(config, name)

    return templates.TemplateResponse(
        "stack.html",
        {
            "request": request,
            "name": name,
            "hosts": hosts,
            "current_host": current_host,
            "compose_content": compose_content,
            "compose_path": str(compose_path) if compose_path else None,
            "env_content": env_content,
            "env_path": str(env_path) if env_path else None,
            "services": services,
            "containers": containers,
            "website_urls": website_urls,
        },
    )


@router.get("/partials/sidebar", response_class=HTMLResponse)
async def sidebar_partial(request: Request) -> HTMLResponse:
    """Sidebar stack list partial."""
    config = get_config()
    templates = get_templates()

    state = load_state(config)

    # Build stack -> host mapping (empty string for multi-host stacks)
    stack_hosts = {
        svc: "" if host_val == "all" or isinstance(host_val, list) else host_val
        for svc, host_val in config.stacks.items()
    }

    return templates.TemplateResponse(
        "partials/sidebar.html",
        {
            "request": request,
            "stacks": sorted(config.stacks.keys()),
            "stack_hosts": stack_hosts,
            "hosts": sorted(config.hosts.keys()),
            "local_host": get_local_host(config),
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
    """Stats cards partial."""
    config = get_config()
    templates = get_templates()

    deployed = load_state(config)
    # Only count stacks that are both in config AND deployed
    running_count = sum(1 for stack in deployed if stack in config.stacks)
    stopped_count = len(config.stacks) - running_count

    return templates.TemplateResponse(
        "partials/stats.html",
        {
            "request": request,
            "hosts": config.hosts,
            "stacks": config.stacks,
            "running_count": running_count,
            "stopped_count": stopped_count,
        },
    )


@router.get("/partials/pending", response_class=HTMLResponse)
async def pending_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Pending operations partial."""
    config = get_config()
    templates = get_templates()

    orphaned = get_orphaned_stacks(config)
    migrations = get_stacks_needing_migration(config)
    not_started = get_stacks_not_in_state(config)

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


@router.get("/partials/stacks-by-host", response_class=HTMLResponse)
async def stacks_by_host_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Stacks by host partial."""
    config = get_config()
    templates = get_templates()

    deployed = load_state(config)
    stacks_by_host = group_running_stacks_by_host(deployed, config.hosts)

    return templates.TemplateResponse(
        "partials/stacks_by_host.html",
        {
            "request": request,
            "hosts": config.hosts,
            "stacks_by_host": stacks_by_host,
            "expanded": expanded,
        },
    )
