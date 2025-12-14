"""Generate Traefik file-provider config from compose labels.

Compose Farm keeps compose files as the source of truth for Traefik routing.
This module reads `traefik.*` labels from a stack's docker-compose.yml and
emits an equivalent file-provider fragment with upstream servers rewritten to
use host-published ports for cross-host reachability.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from .ssh import LOCAL_ADDRESSES

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Config


@dataclass(frozen=True)
class PortMapping:
    """Port mapping for a compose service."""

    target: int
    published: int | None


@dataclass
class TraefikServiceSource:
    """Source information to build an upstream for a Traefik service."""

    traefik_service: str
    stack: str
    compose_service: str
    host_address: str
    ports: list[PortMapping]
    container_port: int | None = None
    scheme: str | None = None


LIST_VALUE_KEYS = {"entrypoints", "middlewares"}
SINGLE_PART = 1
PUBLISHED_TARGET_PARTS = 2
HOST_PUBLISHED_PARTS = 3
MIN_ROUTER_PARTS = 3
MIN_SERVICE_LABEL_PARTS = 6
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _load_env(compose_path: Path) -> dict[str, str]:
    """Load environment variables for compose interpolation."""
    env: dict[str, str] = {}
    env_path = compose_path.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            env[key] = value
    env.update({k: v for k, v in os.environ.items() if isinstance(v, str)})
    return env


def _interpolate(value: str, env: dict[str, str]) -> str:
    """Perform a minimal `${VAR}`/`${VAR:-default}` interpolation."""

    def replace(match: re.Match[str]) -> str:
        var = match.group(1)
        default = match.group(2)
        resolved = env.get(var)
        if resolved:
            return resolved
        return default or ""

    return _VAR_PATTERN.sub(replace, value)


def _normalize_labels(raw: Any, env: dict[str, str]) -> dict[str, str]:
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


def _parse_ports(raw: Any, env: dict[str, str]) -> list[PortMapping]:  # noqa: PLR0912
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

            if len(parts) == SINGLE_PART and parts[0].isdigit():
                target = int(parts[0])
            elif len(parts) == PUBLISHED_TARGET_PARTS and parts[0].isdigit() and parts[1].isdigit():
                published = int(parts[0])
                target = int(parts[1])
            elif len(parts) == HOST_PUBLISHED_PARTS and parts[-2].isdigit() and parts[-1].isdigit():
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


def _parse_value(key: str, raw_value: str) -> Any:
    value = raw_value.strip()
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if value.isdigit():
        return int(value)
    last_segment = key.rsplit(".", 1)[-1]
    if last_segment in LIST_VALUE_KEYS:
        parts = [v.strip() for v in value.split(",")] if "," in value else [value]
        return [part for part in parts if part]
    return value


def _parse_segment(segment: str) -> tuple[str, int | None]:
    if "[" in segment and segment.endswith("]"):
        name, index_raw = segment[:-1].split("[", 1)
        if index_raw.isdigit():
            return name, int(index_raw)
    return segment, None


def _insert(root: dict[str, Any], key_path: list[str], value: Any) -> None:  # noqa: PLR0912
    current: Any = root
    for idx, segment in enumerate(key_path):
        is_last = idx == len(key_path) - 1
        name, list_index = _parse_segment(segment)

        if list_index is None:
            if is_last:
                if not isinstance(current, dict):
                    return
                current[name] = value
            else:
                if not isinstance(current, dict):
                    return
                next_container = current.get(name)
                if not isinstance(next_container, dict):
                    next_container = {}
                    current[name] = next_container
                current = next_container
            continue

        if not isinstance(current, dict):
            return
        container_list = current.get(name)
        if not isinstance(container_list, list):
            container_list = []
            current[name] = container_list
        while len(container_list) <= list_index:
            container_list.append({})
        if is_last:
            container_list[list_index] = value
        else:
            if not isinstance(container_list[list_index], dict):
                container_list[list_index] = {}
            current = container_list[list_index]


def _resolve_published_port(source: TraefikServiceSource) -> tuple[int | None, str | None]:
    """Resolve host-published port for a Traefik service.

    Returns (published_port, warning_message).
    """
    published_ports = [m for m in source.ports if m.published is not None]
    if not published_ports:
        return None, None

    if source.container_port is not None:
        for mapping in published_ports:
            if mapping.target == source.container_port:
                return mapping.published, None
        if len(published_ports) == 1:
            port = published_ports[0].published
            warn = (
                f"[{source.stack}/{source.compose_service}] "
                f"No published port matches container port {source.container_port} "
                f"for Traefik service '{source.traefik_service}', using {port}."
            )
            return port, warn
        return None, (
            f"[{source.stack}/{source.compose_service}] "
            f"No published port matches container port {source.container_port} "
            f"for Traefik service '{source.traefik_service}'."
        )

    if len(published_ports) == 1:
        return published_ports[0].published, None
    return None, (
        f"[{source.stack}/{source.compose_service}] "
        f"Multiple published ports found for Traefik service '{source.traefik_service}', "
        "but no loadbalancer.server.port label to disambiguate."
    )


def _load_stack(config: Config, stack: str) -> tuple[dict[str, Any], dict[str, str], str]:
    compose_path = config.get_compose_path(stack)
    if not compose_path.exists():
        message = f"[{stack}] Compose file not found: {compose_path}"
        raise FileNotFoundError(message)

    env = _load_env(compose_path)
    compose_data = yaml.safe_load(compose_path.read_text()) or {}
    raw_services = compose_data.get("services", {})
    if not isinstance(raw_services, dict):
        return {}, env, config.get_host(stack).address
    return raw_services, env, config.get_host(stack).address


def _finalize_http_services(
    dynamic: dict[str, Any],
    sources: dict[str, TraefikServiceSource],
    warnings: list[str],
) -> None:
    for traefik_service, source in sources.items():
        published_port, warn = _resolve_published_port(source)
        if warn:
            warnings.append(warn)
        if published_port is None:
            warnings.append(
                f"[{source.stack}/{source.compose_service}] "
                f"No published port found for Traefik service '{traefik_service}'. "
                "Add a ports: mapping (e.g., '8080:8080') for cross-host routing."
            )
            continue

        scheme = source.scheme or "http"
        upstream_url = f"{scheme}://{source.host_address}:{published_port}"

        http_section = dynamic.setdefault("http", {})
        services_section = http_section.setdefault("services", {})
        service_cfg = services_section.setdefault(traefik_service, {})
        lb_cfg = service_cfg.setdefault("loadbalancer", {})
        if isinstance(lb_cfg, dict):
            lb_cfg.pop("server", None)
            lb_cfg["servers"] = [{"url": upstream_url}]


def _attach_default_services(
    stack: str,
    compose_service: str,
    routers: dict[str, bool],
    service_names: set[str],
    warnings: list[str],
    dynamic: dict[str, Any],
) -> None:
    if not routers:
        return
    if len(service_names) == 1:
        default_service = next(iter(service_names))
        for router_name, explicit in routers.items():
            if explicit:
                continue
            _insert(dynamic, ["http", "routers", router_name, "service"], default_service)
        return

    if len(service_names) == 0:
        for router_name, explicit in routers.items():
            if not explicit:
                warnings.append(
                    f"[{stack}/{compose_service}] Router '{router_name}' has no service "
                    "and no traefik.http.services labels were found."
                )
        return

    for router_name, explicit in routers.items():
        if explicit:
            continue
        warnings.append(
            f"[{stack}/{compose_service}] Router '{router_name}' has no explicit service "
            "and multiple Traefik services are defined; add "
            f"traefik.http.routers.{router_name}.service."
        )


def _process_router_label(
    key_without_prefix: str,
    routers: dict[str, bool],
) -> None:
    if not key_without_prefix.startswith("http.routers."):
        return
    router_parts = key_without_prefix.split(".")
    if len(router_parts) < MIN_ROUTER_PARTS:
        return
    router_name = router_parts[2]
    router_remainder = router_parts[3:]
    explicit = routers.get(router_name, False)
    if router_remainder == ["service"]:
        explicit = True
    routers[router_name] = explicit


def _process_service_label(
    key_without_prefix: str,
    label_value: str,
    stack: str,
    compose_service: str,
    host_address: str,
    ports: list[PortMapping],
    service_names: set[str],
    sources: dict[str, TraefikServiceSource],
) -> None:
    if not key_without_prefix.startswith("http.services."):
        return
    parts = key_without_prefix.split(".")
    if len(parts) < MIN_SERVICE_LABEL_PARTS:
        return
    traefik_service = parts[2]
    service_names.add(traefik_service)
    remainder = parts[3:]

    source = sources.get(traefik_service)
    if source is None:
        source = TraefikServiceSource(
            traefik_service=traefik_service,
            stack=stack,
            compose_service=compose_service,
            host_address=host_address,
            ports=ports,
        )
        sources[traefik_service] = source

    if remainder == ["loadbalancer", "server", "port"]:
        parsed = _parse_value(key_without_prefix, label_value)
        if isinstance(parsed, int):
            source.container_port = parsed
    elif remainder == ["loadbalancer", "server", "scheme"]:
        source.scheme = str(_parse_value(key_without_prefix, label_value))


def _get_ports_for_service(
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


def _process_service_labels(
    stack: str,
    compose_service: str,
    definition: dict[str, Any],
    all_services: dict[str, Any],
    host_address: str,
    env: dict[str, str],
    dynamic: dict[str, Any],
    sources: dict[str, TraefikServiceSource],
    warnings: list[str],
) -> None:
    labels = _normalize_labels(definition.get("labels"), env)
    if not labels:
        return
    enable_raw = labels.get("traefik.enable")
    if enable_raw is not None and _parse_value("enable", enable_raw) is False:
        return

    ports = _get_ports_for_service(definition, all_services, env)
    routers: dict[str, bool] = {}
    service_names: set[str] = set()

    for label_key, label_value in labels.items():
        if not label_key.startswith("traefik."):
            continue
        if label_key in {"traefik.enable", "traefik.docker.network"}:
            continue

        key_without_prefix = label_key[len("traefik.") :]
        if not key_without_prefix.startswith(("http.", "tcp.", "udp.")):
            continue

        _insert(
            dynamic, key_without_prefix.split("."), _parse_value(key_without_prefix, label_value)
        )
        _process_router_label(key_without_prefix, routers)
        _process_service_label(
            key_without_prefix,
            label_value,
            stack,
            compose_service,
            host_address,
            ports,
            service_names,
            sources,
        )

    _attach_default_services(stack, compose_service, routers, service_names, warnings, dynamic)


MIN_VOLUME_PARTS = 2


def _resolve_host_path(host_path: str, compose_dir: Path) -> str | None:
    """Resolve a host path from volume mount, returning None for named volumes."""
    if host_path.startswith("/"):
        return host_path
    if host_path.startswith(("./", "../")):
        return str((compose_dir / host_path).resolve())
    return None  # Named volume


def _parse_volume_item(
    item: str | dict[str, Any],
    env: dict[str, str],
    compose_dir: Path,
) -> str | None:
    """Parse a single volume item and return host path if it's a bind mount."""
    if isinstance(item, str):
        interpolated = _interpolate(item, env)
        parts = interpolated.split(":")
        if len(parts) >= MIN_VOLUME_PARTS:
            return _resolve_host_path(parts[0], compose_dir)
    elif isinstance(item, dict) and item.get("type") == "bind":
        source = item.get("source")
        if source:
            interpolated = _interpolate(str(source), env)
            return _resolve_host_path(interpolated, compose_dir)
    return None


def parse_host_volumes(config: Config, service: str) -> list[str]:
    """Extract host bind mount paths from a service's compose file.

    Returns a list of absolute host paths used as volume mounts.
    Skips named volumes and resolves relative paths.
    """
    compose_path = config.get_compose_path(service)
    if not compose_path.exists():
        return []

    env = _load_env(compose_path)
    compose_data = yaml.safe_load(compose_path.read_text()) or {}
    raw_services = compose_data.get("services", {})
    if not isinstance(raw_services, dict):
        return []

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
    seen: set[str] = set()
    unique: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def generate_traefik_config(
    config: Config,
    services: list[str],
    *,
    check_all: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Generate Traefik dynamic config from compose labels.

    Args:
        config: The compose-farm config.
        services: List of service names to process.
        check_all: If True, check all services for warnings (ignore host filtering).
                   Used by the check command to validate all traefik labels.

    Returns (config_dict, warnings).

    """
    dynamic: dict[str, Any] = {}
    warnings: list[str] = []
    sources: dict[str, TraefikServiceSource] = {}

    # Determine Traefik's host from service assignment
    traefik_host = None
    if config.traefik_service and not check_all:
        traefik_host = config.services.get(config.traefik_service)

    for stack in services:
        raw_services, env, host_address = _load_stack(config, stack)
        stack_host = config.services.get(stack)

        # Skip services on Traefik's host - docker provider handles them directly
        # (unless check_all is True, for validation purposes)
        if not check_all:
            if host_address.lower() in LOCAL_ADDRESSES:
                continue
            if traefik_host and stack_host == traefik_host:
                continue

        for compose_service, definition in raw_services.items():
            if not isinstance(definition, dict):
                continue
            _process_service_labels(
                stack,
                compose_service,
                definition,
                raw_services,
                host_address,
                env,
                dynamic,
                sources,
                warnings,
            )

    _finalize_http_services(dynamic, sources, warnings)
    return dynamic, warnings
