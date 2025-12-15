"""State tracking for deployed services."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Generator

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


@contextlib.contextmanager
def _modify_state(config: Config) -> Generator[dict[str, str | list[str]], None, None]:
    """Context manager to load, modify, and save state."""
    state = load_state(config)
    yield state
    save_state(config, state)


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
    with _modify_state(config) as state:
        state[service] = host


def set_multi_host_service(config: Config, service: str, hosts: list[str]) -> None:
    """Record that a multi-host service is deployed on multiple hosts."""
    with _modify_state(config) as state:
        state[service] = hosts


def remove_service(config: Config, service: str) -> None:
    """Remove a service from the state (after down)."""
    with _modify_state(config) as state:
        state.pop(service, None)


def add_service_to_host(config: Config, service: str, host: str) -> None:
    """Add a specific host to a service's state.

    For multi-host services, adds the host to the list if not present.
    For single-host services, sets the host.
    """
    with _modify_state(config) as state:
        current = state.get(service)

        if config.is_multi_host(service):
            # Multi-host: add to list if not present
            if isinstance(current, list):
                if host not in current:
                    state[service] = [*current, host]
            else:
                state[service] = [host]
        else:
            # Single-host: just set it
            state[service] = host


def remove_service_from_host(config: Config, service: str, host: str) -> None:
    """Remove a specific host from a service's state.

    For multi-host services, removes just that host from the list.
    For single-host services, removes the service entirely if host matches.
    """
    with _modify_state(config) as state:
        current = state.get(service)
        if current is None:
            return

        if isinstance(current, list):
            # Multi-host: remove this host from list
            remaining = [h for h in current if h != host]
            if remaining:
                state[service] = remaining
            else:
                state.pop(service, None)
        elif current == host:
            # Single-host: remove if matches
            state.pop(service, None)


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
