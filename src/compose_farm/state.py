"""State tracking for deployed services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .config import Config


def load_state(config: Config) -> dict[str, str | list[str]]:
    """Load the current deployment state.

    Returns a dict mapping service names to host name(s).
    Multi-host services store a list of hosts.
    """
    state_path = config.get_state_path()
    if not state_path.exists():
        return {}

    with state_path.open() as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    deployed: dict[str, str | list[str]] = data.get("deployed", {})
    return deployed


def _sorted_dict(d: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
    """Return a dictionary sorted by keys."""
    return dict(sorted(d.items(), key=lambda item: item[0]))


def save_state(config: Config, deployed: dict[str, str | list[str]]) -> None:
    """Save the deployment state."""
    state_path = config.get_state_path()
    with state_path.open("w") as f:
        yaml.safe_dump({"deployed": _sorted_dict(deployed)}, f, sort_keys=False)


def get_service_host(config: Config, service: str) -> str | None:
    """Get the host where a service is currently deployed.

    For multi-host services, returns the first host or None.
    """
    state = load_state(config)
    value = state.get(service)
    if value is None:
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value


def get_service_hosts(config: Config, service: str) -> list[str]:
    """Get all hosts where a service is currently deployed."""
    state = load_state(config)
    value = state.get(service)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def set_service_host(config: Config, service: str, host: str) -> None:
    """Record that a service is deployed on a host."""
    state = load_state(config)
    state[service] = host
    save_state(config, state)


def set_multi_host_service(config: Config, service: str, hosts: list[str]) -> None:
    """Record that a multi-host service is deployed on multiple hosts."""
    state = load_state(config)
    state[service] = hosts
    save_state(config, state)


def remove_service(config: Config, service: str) -> None:
    """Remove a service from the state (after down)."""
    state = load_state(config)
    state.pop(service, None)
    save_state(config, state)


def get_services_needing_migration(config: Config) -> list[str]:
    """Get services where current host differs from configured host.

    Multi-host services are never considered for migration.
    """
    needs_migration = []
    for service in config.services:
        # Skip multi-host services
        if config.is_multi_host(service):
            continue

        configured_host = config.get_hosts(service)[0]
        current_host = get_service_host(config, service)
        if current_host and current_host != configured_host:
            needs_migration.append(service)
    return needs_migration
