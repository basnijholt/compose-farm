"""State tracking for deployed stacks."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from .config import Config


def group_stacks_by_host(
    stacks: dict[str, str | list[str]],
    hosts: Mapping[str, object],
    all_hosts: list[str] | None = None,
) -> dict[str, list[str]]:
    """Group stacks by their assigned host(s).

    For multi-host stacks (list or "all"), the stack appears in multiple host lists.
    """
    by_host: dict[str, list[str]] = {h: [] for h in hosts}
    for stack, host_value in stacks.items():
        if isinstance(host_value, list):
            for host_name in host_value:
                if host_name in by_host:
                    by_host[host_name].append(stack)
        elif host_value == "all" and all_hosts:
            for host_name in all_hosts:
                if host_name in by_host:
                    by_host[host_name].append(stack)
        elif host_value in by_host:
            by_host[host_value].append(stack)
    return by_host


def group_running_stacks_by_host(
    state: dict[str, str | list[str]],
    hosts: Mapping[str, object],
) -> dict[str, list[str]]:
    """Group running stacks by host, filtering out hosts with no stacks."""
    by_host = group_stacks_by_host(state, hosts)
    return {h: svcs for h, svcs in by_host.items() if svcs}


def load_state(config: Config) -> dict[str, str | list[str]]:
    """Load the current deployment state.

    Returns a dict mapping stack names to host name(s).
    Multi-host stacks store a list of hosts.
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


def get_stack_host(config: Config, stack: str) -> str | None:
    """Get the host where a stack is currently deployed.

    For multi-host stacks, returns the first host or None.
    """
    state = load_state(config)
    value = state.get(stack)
    if value is None:
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value


def set_stack_host(config: Config, stack: str, host: str) -> None:
    """Record that a stack is deployed on a host."""
    with _modify_state(config) as state:
        state[stack] = host


def set_multi_host_stack(config: Config, stack: str, hosts: list[str]) -> None:
    """Record that a multi-host stack is deployed on multiple hosts."""
    with _modify_state(config) as state:
        state[stack] = hosts


def remove_stack(config: Config, stack: str) -> None:
    """Remove a stack from the state (after down)."""
    with _modify_state(config) as state:
        state.pop(stack, None)


def get_stacks_needing_migration(config: Config) -> list[str]:
    """Get stacks where current host differs from configured host.

    Multi-host stacks are never considered for migration.
    """
    needs_migration = []
    for stack in config.stacks:
        # Skip multi-host stacks
        if config.is_multi_host(stack):
            continue

        configured_host = config.get_hosts(stack)[0]
        current_host = get_stack_host(config, stack)
        if current_host and current_host != configured_host:
            needs_migration.append(stack)
    return needs_migration


def get_orphaned_stacks(config: Config) -> dict[str, str | list[str]]:
    """Get stacks that are in state but not in config.

    These are stacks that were previously deployed but have been
    removed from the config file (e.g., commented out).

    Returns a dict mapping stack name to host(s) where it's deployed.
    """
    state = load_state(config)
    return {stack: hosts for stack, hosts in state.items() if stack not in config.stacks}


def get_stacks_not_in_state(config: Config) -> list[str]:
    """Get stacks that are in config but not in state.

    These are stacks that should be running but aren't tracked
    (e.g., newly added to config, or previously stopped as orphans).
    """
    state = load_state(config)
    return [stack for stack in config.stacks if stack not in state]
