"""JSON API routes."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse

from compose_farm.state import get_service_host, load_state
from compose_farm.web.app import get_config, reload_config

router = APIRouter(tags=["api"])


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
