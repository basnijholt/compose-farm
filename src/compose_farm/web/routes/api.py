"""JSON API routes."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import yaml
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from compose_farm.state import get_service_host, load_state
from compose_farm.web.app import get_config, reload_config

router = APIRouter(tags=["api"])


def _get_compose_services(config: Any, service: str, hosts: list[str]) -> list[dict[str, Any]]:
    """Get container info from compose file (fast, local read).

    Returns one entry per container per host for multi-host services.
    """
    import yaml as pyyaml

    compose_path = config.get_compose_path(service)
    if not compose_path or not compose_path.exists():
        return []

    compose_data = pyyaml.safe_load(compose_path.read_text()) or {}
    raw_services = compose_data.get("services", {})
    if not isinstance(raw_services, dict):
        return []

    # Project name is the directory name (docker compose default)
    project_name = compose_path.parent.name

    containers = []
    for host in hosts:
        for svc_name, svc_def in raw_services.items():
            # Use container_name if set, otherwise default to {project}-{service}-1
            if isinstance(svc_def, dict) and svc_def.get("container_name"):
                container_name = svc_def["container_name"]
            else:
                container_name = f"{project_name}-{svc_name}-1"
            containers.append(
                {
                    "Name": container_name,
                    "Service": svc_name,
                    "Host": host,
                    "State": "unknown",  # Status requires Docker query
                }
            )
    return containers


async def _get_container_states(
    config: Any, service: str, containers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Query Docker for actual container states on a single host."""
    from compose_farm.executor import run_compose_on_host

    if not containers:
        return containers

    # All containers should be on the same host
    host_name = containers[0]["Host"]

    result = await run_compose_on_host(config, service, host_name, "ps --format json", stream=False)
    if not result.success:
        return containers

    # Build state map
    state_map: dict[str, str] = {}
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            with contextlib.suppress(json.JSONDecodeError):
                data = json.loads(line)
                state_map[data.get("Name", "")] = data.get("State", "unknown")

    # Update container states
    for c in containers:
        if c["Name"] in state_map:
            c["State"] = state_map[c["Name"]]

    return containers


@router.get("/services")  # type: ignore[misc]
async def list_services() -> list[dict[str, Any]]:
    """List all services with their status."""
    config = get_config()
    deployed = load_state(config)

    services = []
    for name in sorted(config.services.keys()):
        hosts = config.get_hosts(name)
        current_host = deployed.get(name)
        compose_path = config.get_compose_path(name)

        services.append(
            {
                "name": name,
                "configured_hosts": hosts,
                "current_host": current_host,
                "running": current_host is not None,
                "compose_path": str(compose_path) if compose_path else None,
            }
        )

    return services


@router.get("/service/{name}")  # type: ignore[misc]
async def get_service(name: str) -> dict[str, Any]:
    """Get service details."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    hosts = config.get_hosts(name)
    current_host = get_service_host(config, name)
    compose_path = config.get_compose_path(name)

    return {
        "name": name,
        "configured_hosts": hosts,
        "current_host": current_host,
        "running": current_host is not None,
        "compose_path": str(compose_path) if compose_path else None,
    }


@router.get("/service/{name}/containers", response_class=HTMLResponse)  # type: ignore[misc]
async def get_containers(name: str, host: str | None = None) -> HTMLResponse:
    """Get containers for a service as HTML buttons.

    If host is specified, queries Docker for that host's status.
    Otherwise returns all hosts with loading spinners that auto-fetch.
    """
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    # Get hosts where service is running from state
    state = load_state(config)
    current_hosts = state.get(name)
    if not current_hosts:
        return HTMLResponse('<span class="text-base-content/60">Service not running</span>')

    all_hosts = current_hosts if isinstance(current_hosts, list) else [current_hosts]

    # If host specified, return just that host's containers with status
    if host:
        if host not in all_hosts:
            return HTMLResponse(f'<span class="text-error">Host {host} not found</span>')

        containers = _get_compose_services(config, name, [host])
        containers = await _get_container_states(config, name, containers)
        return HTMLResponse(_render_host_containers(name, host, containers, show_header=False))

    # Initial load: return all hosts with loading spinners, each fetches its own status
    html_parts = []
    is_multi_host = len(all_hosts) > 1

    for h in all_hosts:
        host_id = f"containers-{name}-{h}".replace(".", "-")
        containers = _get_compose_services(config, name, [h])

        if is_multi_host:
            html_parts.append(f'<div class="font-semibold text-sm mt-3 mb-1">{h}</div>')

        # Container for this host that auto-fetches its own status
        html_parts.append(f"""
            <div id="{host_id}"
                 hx-get="/api/service/{name}/containers?host={h}"
                 hx-trigger="load"
                 hx-target="this"
                 hx-select="unset"
                 hx-swap="innerHTML">
                {_render_host_containers(name, h, containers, show_header=False)}
            </div>
        """)

    return HTMLResponse("".join(html_parts))


def _render_host_containers(
    service: str, host: str, containers: list[dict[str, Any]], *, show_header: bool
) -> str:
    """Render HTML for containers on a single host."""
    html_parts = []

    if show_header:
        html_parts.append(f'<div class="font-semibold text-sm mt-3 mb-1">{host}</div>')

    for c in containers:
        container_name = c.get("Name", "unknown")
        state = c.get("State", "unknown")

        if state == "running":
            badge = '<span class="badge badge-success">running</span>'
        elif state == "unknown":
            badge = '<span class="badge badge-ghost"><span class="loading loading-spinner loading-xs"></span></span>'
        else:
            badge = f'<span class="badge badge-warning">{state}</span>'

        html_parts.append(f"""
            <div class="flex items-center gap-2 mb-2">
                {badge}
                <code class="text-sm flex-1">{container_name}</code>
                <button class="btn btn-sm btn-outline"
                        onclick="initExecTerminal('{service}', '{container_name}', '{host}')">
                    Shell
                </button>
            </div>
        """)

    return "".join(html_parts)


@router.get("/service/{name}/compose", response_class=PlainTextResponse)  # type: ignore[misc]
async def get_compose(name: str) -> str:
    """Get compose file content."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    compose_path = config.get_compose_path(name)
    if not compose_path or not compose_path.exists():
        raise HTTPException(status_code=404, detail="Compose file not found")

    return compose_path.read_text()


@router.put("/service/{name}/compose")  # type: ignore[misc]
async def save_compose(
    name: str, content: str = Body(..., media_type="text/plain")
) -> dict[str, Any]:
    """Save compose file content."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    compose_path = config.get_compose_path(name)
    if not compose_path:
        raise HTTPException(status_code=404, detail="Compose file not found")

    # Validate YAML before saving
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e

    compose_path.write_text(content)

    return {"success": True, "message": "Compose file saved"}


@router.get("/service/{name}/env", response_class=PlainTextResponse)  # type: ignore[misc]
async def get_env(name: str) -> str:
    """Get .env file content."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    compose_path = config.get_compose_path(name)
    if not compose_path:
        raise HTTPException(status_code=404, detail="Compose file not found")

    env_path = compose_path.parent / ".env"
    if not env_path.exists():
        return ""

    return env_path.read_text()


@router.put("/service/{name}/env")  # type: ignore[misc]
async def save_env(name: str, content: str = Body(..., media_type="text/plain")) -> dict[str, Any]:
    """Save .env file content."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    compose_path = config.get_compose_path(name)
    if not compose_path:
        raise HTTPException(status_code=404, detail="Compose file not found")

    env_path = compose_path.parent / ".env"
    env_path.write_text(content)

    return {"success": True, "message": ".env file saved"}


@router.get("/config")  # type: ignore[misc]
async def get_config_route() -> dict[str, Any]:
    """Get current configuration."""
    config = get_config()
    return config.model_dump(mode="json")


@router.put("/config")  # type: ignore[misc]
async def save_config(
    content: str = Body(..., media_type="text/plain"),
) -> dict[str, Any]:
    """Save compose-farm.yaml config file."""
    config = get_config()

    if not config.config_path:
        raise HTTPException(status_code=404, detail="Config path not set")

    # Validate YAML before saving
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e

    config.config_path.write_text(content)

    # Reload config so subsequent requests see updated values
    reload_config()

    return {"success": True, "message": "Config saved"}


@router.get("/state")  # type: ignore[misc]
async def get_state() -> dict[str, Any]:
    """Get current deployment state."""
    config = get_config()
    return load_state(config)
