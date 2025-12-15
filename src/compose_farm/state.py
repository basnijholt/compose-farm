"""State tracking for deployed services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .config import Config


def load_state(config: Config) -> dict[str, str]:
    """Load the current deployment state.

    Returns a dict mapping service names to host names.
    """
    state_path = config.get_state_path()
    if not state_path.exists():
        return {}

    with state_path.open() as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    deployed: dict[str, str] = data.get("deployed", {})
    return deployed


def _sorted_dict(d: dict[str, str]) -> dict[str, str]:
    """Return a dictionary sorted by keys."""
    return dict(sorted(d.items(), key=lambda item: item[0]))


def save_state(config: Config, deployed: dict[str, str]) -> None:
    """Save the deployment state."""
    state_path = config.get_state_path()
    with state_path.open("w") as f:
        yaml.safe_dump({"deployed": _sorted_dict(deployed)}, f, sort_keys=False)


def get_service_host(config: Config, service: str) -> str | None:
    """Get the host where a service is currently deployed."""
    state = load_state(config)
    return state.get(service)


def set_service_host(config: Config, service: str, host: str) -> None:
    """Record that a service is deployed on a host."""
    state = load_state(config)
    state[service] = host
    save_state(config, state)


def remove_service(config: Config, service: str) -> None:
    """Remove a service from the state (after down)."""
    state = load_state(config)
    state.pop(service, None)
    save_state(config, state)


def get_services_needing_migration(config: Config) -> list[str]:
    """Get services where current host differs from configured host."""
    state = load_state(config)
    needs_migration = []
    for service, configured_host in config.services.items():
        current_host = state.get(service)
        if current_host and current_host != configured_host:
            needs_migration.append(service)
    return needs_migration
