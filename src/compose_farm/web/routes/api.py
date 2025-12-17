"""JSON API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse

from compose_farm.web.app import get_config

router = APIRouter(tags=["api"])


@router.get("/services")
async def list_services() -> list[dict[str, Any]]:
    """List all services with their status."""
    config = get_config()

    from compose_farm.state import load_state

    state = load_state(config)
    deployed = state.get("deployed", {})

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


@router.get("/service/{name}")
async def get_service(name: str) -> dict[str, Any]:
    """Get service details."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    from compose_farm.state import get_service_host

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


@router.get("/service/{name}/compose", response_class=PlainTextResponse)
async def get_compose(name: str) -> str:
    """Get compose file content."""
    config = get_config()

    if name not in config.services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")

    compose_path = config.get_compose_path(name)
    if not compose_path or not compose_path.exists():
        raise HTTPException(status_code=404, detail="Compose file not found")

    return compose_path.read_text()


@router.put("/service/{name}/compose")
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
    import yaml

    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e

    compose_path.write_text(content)

    return {"success": True, "message": "Compose file saved"}


@router.get("/config")
async def get_config_route() -> dict[str, Any]:
    """Get current configuration."""
    config = get_config()
    return config.model_dump(mode="json")


@router.get("/state")
async def get_state() -> dict[str, Any]:
    """Get current deployment state."""
    config = get_config()

    from compose_farm.state import load_state

    return load_state(config)
