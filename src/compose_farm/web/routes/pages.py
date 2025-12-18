"""HTML page routes."""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from compose_farm.state import (
    get_orphaned_services,
    get_service_host,
    get_services_needing_migration,
    get_services_not_in_state,
    load_state,
)
from compose_farm.web.deps import get_config, get_templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Dashboard page - combined view of all cluster info."""
    templates = get_templates()

    # Try to load config, handle errors gracefully
    config_error = None
    try:
        config = get_config()
    except (ValidationError, FileNotFoundError) as e:
        # Extract error message
        if isinstance(e, ValidationError):
            config_error = "; ".join(err.get("msg", str(err)) for err in e.errors())
        else:
            config_error = str(e)

        # Read raw config content for the editor (find path manually)
        import os  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        from compose_farm.paths import xdg_config_home  # noqa: PLC0415

        config_content = ""
        for p in [
            Path(os.environ.get("CF_CONFIG", "")),
            Path("compose-farm.yaml"),
            xdg_config_home() / "compose-farm" / "compose-farm.yaml",
        ]:
            if p.exists() and p.is_file():
                config_content = p.read_text()
                break

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

    deployed = load_state(config)
    running_count = len(deployed)
    stopped_count = len(config.services) - running_count
    orphaned = get_orphaned_services(config)
    migrations = get_services_needing_migration(config)
    not_started = get_services_not_in_state(config)

    services_by_host: dict[str, list[str]] = {}
    for svc, host in deployed.items():
        if isinstance(host, list):
            for h in host:
                services_by_host.setdefault(h, []).append(svc)
        else:
            services_by_host.setdefault(host, []).append(svc)

    config_content = ""
    if config.config_path and config.config_path.exists():
        config_content = config.config_path.read_text()

    state_content = yaml.dump({"deployed": deployed}, default_flow_style=False, sort_keys=False)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "config_error": None,
            "hosts": config.hosts,
            "services": config.services,
            "config_content": config_content,
            "state_content": state_content,
            "running_count": running_count,
            "stopped_count": stopped_count,
            "orphaned": orphaned,
            "migrations": migrations,
            "not_started": not_started,
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
        if isinstance(e, ValidationError):
            error = "; ".join(err.get("msg", str(err)) for err in e.errors())
        else:
            error = str(e)
        return templates.TemplateResponse(
            "partials/config_error.html", {"request": request, "config_error": error}
        )


@router.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request) -> HTMLResponse:
    """Stats cards partial."""
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


@router.get("/partials/pending", response_class=HTMLResponse)
async def pending_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Pending operations partial."""
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


@router.get("/partials/services-by-host", response_class=HTMLResponse)
async def services_by_host_partial(request: Request, expanded: bool = True) -> HTMLResponse:
    """Services by host partial."""
    config = get_config()
    templates = get_templates()

    deployed = load_state(config)

    # Group services by host
    services_by_host: dict[str, list[str]] = {}
    for svc, host in deployed.items():
        if isinstance(host, list):
            for h in host:
                services_by_host.setdefault(h, []).append(svc)
        else:
            services_by_host.setdefault(host, []).append(svc)

    return templates.TemplateResponse(
        "partials/services_by_host.html",
        {
            "request": request,
            "hosts": config.hosts,
            "services_by_host": services_by_host,
            "expanded": expanded,
        },
    )
