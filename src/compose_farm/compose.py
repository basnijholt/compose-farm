"""Compose file parsing utilities.

Handles .env loading, variable interpolation, port/volume/network extraction.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from dotenv import dotenv_values

if TYPE_CHECKING:
    from .config import Config

# Port parsing constants
_SINGLE_PART = 1
_PUBLISHED_TARGET_PARTS = 2
_HOST_PUBLISHED_PARTS = 3
_MIN_VOLUME_PARTS = 2

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


@dataclass(frozen=True)
class PortMapping:
    """Port mapping for a compose service."""

    target: int
    published: int | None


def _load_env(compose_path: Path) -> dict[str, str]:
    """Load environment variables for compose interpolation.

    Reads from .env file in the same directory as compose file,
    then overlays current environment variables.
    """
    env_path = compose_path.parent / ".env"
    env: dict[str, str] = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    env.update({k: v for k, v in os.environ.items() if isinstance(v, str)})
    return env


def parse_compose_data(content: str) -> dict[str, Any]:
    """Parse compose YAML content into a dict."""
    compose_data = yaml.safe_load(content) or {}
    return compose_data if isinstance(compose_data, dict) else {}


def load_compose_data(compose_path: Path) -> dict[str, Any]:
    """Load compose YAML from a file path."""
    return parse_compose_data(compose_path.read_text())


def load_compose_data_for_stack(config: Config, stack: str) -> tuple[Path, dict[str, Any]]:
    """Load compose YAML for a stack, returning (path, data)."""
    compose_path = config.get_compose_path(stack)
    if not compose_path.exists():
        return compose_path, {}
    return compose_path, load_compose_data(compose_path)


def extract_services(compose_data: dict[str, Any]) -> dict[str, Any]:
    """Extract services mapping from compose data."""
    raw_services = compose_data.get("services", {})
    return raw_services if isinstance(raw_services, dict) else {}


def _interpolate(value: str, env: dict[str, str]) -> str:
    """Perform ${VAR} and ${VAR:-default} interpolation."""

    def replace(match: re.Match[str]) -> str:
        var = match.group(1)
        default = match.group(2)
        resolved = env.get(var)
        if resolved:
            return resolved
        return default or ""

    return _VAR_PATTERN.sub(replace, value)


def _parse_ports(raw: Any, env: dict[str, str]) -> list[PortMapping]:  # noqa: PLR0912
    """Parse port specifications from compose file.

    Handles string formats like "8080", "8080:80", "0.0.0.0:8080:80",
    and dict formats with target/published keys.
    """
    if raw is None:
        return []
    mappings: list[PortMapping] = []

    items = raw if isinstance(raw, list) else [raw]

    for item in items:
        if isinstance(item, str):
            interpolated = _interpolate(item, env)
            port_spec, _, _ = interpolated.partition("/")
            parts = port_spec.split(":")
            published: int | None = None
            target: int | None = None

            if len(parts) == _SINGLE_PART and parts[0].isdigit():
                target = int(parts[0])
            elif (
                len(parts) == _PUBLISHED_TARGET_PARTS and parts[0].isdigit() and parts[1].isdigit()
            ):
                published = int(parts[0])
                target = int(parts[1])
            elif (
                len(parts) == _HOST_PUBLISHED_PARTS and parts[-2].isdigit() and parts[-1].isdigit()
            ):
                published = int(parts[-2])
                target = int(parts[-1])

            if target is not None:
                mappings.append(PortMapping(target=target, published=published))
        elif isinstance(item, dict):
            target_raw = item.get("target")
            if isinstance(target_raw, str):
                target_raw = _interpolate(target_raw, env)
            if target_raw is None:
                continue
            try:
                target_val = int(str(target_raw))
            except (TypeError, ValueError):
                continue

            published_raw = item.get("published")
            if isinstance(published_raw, str):
                published_raw = _interpolate(published_raw, env)
            published_val: int | None
            try:
                published_val = int(str(published_raw)) if published_raw is not None else None
            except (TypeError, ValueError):
                published_val = None
            mappings.append(PortMapping(target=target_val, published=published_val))

    return mappings


def _resolve_host_path(host_path: str, compose_dir: Path) -> str | None:
    """Resolve a host path from volume mount, returning None for named volumes."""
    if host_path.startswith("/"):
        return host_path
    if host_path.startswith(("./", "../")):
        return str((compose_dir / host_path).resolve())
    return None  # Named volume


def _is_socket(path: str) -> bool:
    """Check if a path is a socket (e.g., SSH agent socket)."""
    try:
        return stat.S_ISSOCK(Path(path).stat().st_mode)
    except (FileNotFoundError, PermissionError, OSError):
        return False


def _parse_volume_item(
    item: str | dict[str, Any],
    env: dict[str, str],
    compose_dir: Path,
) -> str | None:
    """Parse a single volume item and return host path if it's a bind mount.

    Skips socket paths (e.g., SSH_AUTH_SOCK) since they're machine-local
    and shouldn't be validated on remote hosts.
    """
    host_path: str | None = None

    if isinstance(item, str):
        interpolated = _interpolate(item, env)
        parts = interpolated.split(":")
        if len(parts) >= _MIN_VOLUME_PARTS:
            host_path = _resolve_host_path(parts[0], compose_dir)
    elif isinstance(item, dict) and item.get("type") == "bind":
        source = item.get("source")
        if source:
            interpolated = _interpolate(str(source), env)
            host_path = _resolve_host_path(interpolated, compose_dir)

    # Skip sockets - they're machine-local (e.g., SSH agent)
    if host_path and _is_socket(host_path):
        return None

    return host_path


def parse_host_volumes(config: Config, stack: str) -> list[str]:
    """Extract host bind mount paths from a stack's compose file.

    Returns a list of absolute host paths used as volume mounts.
    Skips named volumes and resolves relative paths.
    """
    compose_path, compose_data = load_compose_data_for_stack(config, stack)
    if not compose_path.exists():
        return []

    raw_services = extract_services(compose_data)
    if not raw_services:
        return []

    env = _load_env(compose_path)
    paths: list[str] = []
    compose_dir = compose_path.parent

    for definition in raw_services.values():
        if not isinstance(definition, dict):
            continue

        volumes = definition.get("volumes")
        if not volumes:
            continue

        items = volumes if isinstance(volumes, list) else [volumes]
        for item in items:
            host_path = _parse_volume_item(item, env, compose_dir)
            if host_path:
                paths.append(host_path)

    # Return unique paths, preserving order
    return list(dict.fromkeys(paths))


def parse_devices(config: Config, stack: str) -> list[str]:
    """Extract host device paths from a stack's compose file.

    Returns a list of host device paths (e.g., /dev/dri, /dev/dri/renderD128).
    """
    compose_path, compose_data = load_compose_data_for_stack(config, stack)
    if not compose_path.exists():
        return []

    raw_services = extract_services(compose_data)
    if not raw_services:
        return []

    env = _load_env(compose_path)
    devices: list[str] = []
    for definition in raw_services.values():
        if not isinstance(definition, dict):
            continue

        device_list = definition.get("devices")
        if not device_list or not isinstance(device_list, list):
            continue

        for item in device_list:
            if not isinstance(item, str):
                continue
            interpolated = _interpolate(item, env)
            # Format: host_path:container_path[:options]
            parts = interpolated.split(":")
            if parts:
                host_path = parts[0]
                if host_path.startswith("/dev/"):
                    devices.append(host_path)

    # Return unique devices, preserving order
    return list(dict.fromkeys(devices))


def parse_external_networks(config: Config, stack: str) -> list[str]:
    """Extract external network names from a stack's compose file.

    Returns a list of network names marked as external: true.
    """
    compose_path, compose_data = load_compose_data_for_stack(config, stack)
    if not compose_path.exists():
        return []

    networks = compose_data.get("networks", {})
    if not isinstance(networks, dict):
        return []

    external_networks: list[str] = []
    for name, definition in networks.items():
        if isinstance(definition, dict) and definition.get("external") is True:
            external_networks.append(name)

    return external_networks


def load_compose_services(
    config: Config,
    stack: str,
) -> tuple[dict[str, Any], dict[str, str], str]:
    """Load services from a compose file with environment interpolation.

    Returns (services_dict, env_dict, host_address).
    """
    compose_path, compose_data = load_compose_data_for_stack(config, stack)
    if not compose_path.exists():
        message = f"[{stack}] Compose file not found: {compose_path}"
        raise FileNotFoundError(message)

    env = _load_env(compose_path)
    raw_services = extract_services(compose_data)
    if not raw_services:
        return {}, env, config.get_host(stack).address
    return raw_services, env, config.get_host(stack).address


def normalize_labels(raw: Any, env: dict[str, str]) -> dict[str, str]:
    """Normalize labels from list or dict format, with interpolation."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {
            _interpolate(str(k), env): _interpolate(str(v), env)
            for k, v in raw.items()
            if k is not None
        }
    if isinstance(raw, list):
        labels: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, str) or "=" not in item:
                continue
            key_raw, value_raw = item.split("=", 1)
            key = _interpolate(key_raw.strip(), env)
            value = _interpolate(value_raw.strip(), env)
            labels[key] = value
        return labels
    return {}


def get_ports_for_service(
    definition: dict[str, Any],
    all_services: dict[str, Any],
    env: dict[str, str],
) -> list[PortMapping]:
    """Get ports for a service, following network_mode: service:X if present."""
    network_mode = definition.get("network_mode", "")
    if isinstance(network_mode, str) and network_mode.startswith("service:"):
        # Service uses another service's network - get ports from that service
        ref_service = network_mode[len("service:") :]
        if ref_service in all_services:
            ref_def = all_services[ref_service]
            if isinstance(ref_def, dict):
                return _parse_ports(ref_def.get("ports"), env)
    return _parse_ports(definition.get("ports"), env)


def get_container_name(
    service_name: str,
    service_def: dict[str, Any] | None,
    project_name: str,
) -> str:
    """Get the container name for a service.

    Uses container_name from compose if set, otherwise defaults to {project}-{service}-1.
    This matches Docker Compose's default naming convention.
    """
    if isinstance(service_def, dict) and service_def.get("container_name"):
        return str(service_def["container_name"])
    return f"{project_name}-{service_name}-1"
