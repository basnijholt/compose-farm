"""State tracking for deployed services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _get_state_path() -> Path:
    """Get the path to the state file."""
    state_dir = Path.home() / ".config" / "compose-farm"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "state.yaml"


def load_state() -> dict[str, str]:
    """Load the current deployment state.

    Returns a dict mapping service names to host names.
    """
    state_path = _get_state_path()
    if not state_path.exists():
        return {}

    with state_path.open() as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    deployed: dict[str, str] = data.get("deployed", {})
    return deployed


def save_state(deployed: dict[str, str]) -> None:
    """Save the deployment state."""
    state_path = _get_state_path()
    with state_path.open("w") as f:
        yaml.safe_dump({"deployed": deployed}, f, sort_keys=False)


def get_service_host(service: str) -> str | None:
    """Get the host where a service is currently deployed."""
    state = load_state()
    return state.get(service)


def set_service_host(service: str, host: str) -> None:
    """Record that a service is deployed on a host."""
    state = load_state()
    state[service] = host
    save_state(state)


def remove_service(service: str) -> None:
    """Remove a service from the state (after down)."""
    state = load_state()
    state.pop(service, None)
    save_state(state)
