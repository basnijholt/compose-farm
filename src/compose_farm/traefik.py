"""Generate Traefik file-provider config from compose labels.

Compose Farm keeps compose files as the source of truth for Traefik routing.
This module reads `traefik.*` labels from a stack's docker-compose.yml and
emits an equivalent file-provider fragment with upstream servers rewritten to
use host-published ports for cross-host reachability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from .compose import (
    PortMapping,
    get_ports_for_service,
    load_compose_services,
    normalize_labels,
)
from .executor import LOCAL_ADDRESSES

if TYPE_CHECKING:
    from .config import Config


@dataclass
class _TraefikServiceSource:
    """Source information to build an upstream for a Traefik service."""

    traefik_service: str
    stack: str
    compose_service: str
    host_address: str
    ports: list[PortMapping]
    container_port: int | None = None
    scheme: str | None = None


_LIST_VALUE_KEYS = {"entrypoints", "middlewares"}
_MIN_ROUTER_PARTS = 3
_MIN_SERVICE_LABEL_PARTS = 6


def _parse_value(key: str, raw_value: str) -> Any:
    value = raw_value.strip()
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if value.isdigit():
        return int(value)
    last_segment = key.rsplit(".", 1)[-1]
    if last_segment in _LIST_VALUE_KEYS:
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


def _resolve_published_port(source: _TraefikServiceSource) -> tuple[int | None, str | None]:
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


def _finalize_http_services(
    dynamic: dict[str, Any],
    sources: dict[str, _TraefikServiceSource],
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
    if len(router_parts) < _MIN_ROUTER_PARTS:
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
    sources: dict[str, _TraefikServiceSource],
) -> None:
    if not key_without_prefix.startswith("http.services."):
        return
    parts = key_without_prefix.split(".")
    if len(parts) < _MIN_SERVICE_LABEL_PARTS:
        return
    traefik_service = parts[2]
    service_names.add(traefik_service)
    remainder = parts[3:]

    source = sources.get(traefik_service)
    if source is None:
        source = _TraefikServiceSource(
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


def _process_service_labels(
    stack: str,
    compose_service: str,
    definition: dict[str, Any],
    all_services: dict[str, Any],
    host_address: str,
    env: dict[str, str],
    dynamic: dict[str, Any],
    sources: dict[str, _TraefikServiceSource],
    warnings: list[str],
) -> None:
    labels = normalize_labels(definition.get("labels"), env)
    if not labels:
        return
    enable_raw = labels.get("traefik.enable")
    if enable_raw is not None and _parse_value("enable", enable_raw) is False:
        return

    ports = get_ports_for_service(definition, all_services, env)
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


def generate_traefik_config(
    config: Config,
    stacks: list[str],
    *,
    check_all: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Generate Traefik dynamic config from compose labels.

    Args:
        config: The compose-farm config.
        stacks: List of stack names to process.
        check_all: If True, check all stacks for warnings (ignore host filtering).
                   Used by the check command to validate all traefik labels.

    Returns (config_dict, warnings).

    """
    dynamic: dict[str, Any] = {}
    warnings: list[str] = []
    sources: dict[str, _TraefikServiceSource] = {}

    # Determine Traefik's host from service assignment
    traefik_host = None
    if config.traefik_stack and not check_all:
        traefik_host = config.stacks.get(config.traefik_stack)

    for stack in stacks:
        raw_services, env, host_address = load_compose_services(config, stack)
        stack_host = config.stacks.get(stack)

        # Skip stacks on Traefik's host - docker provider handles them directly
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


_TRAEFIK_CONFIG_HEADER = """\
# Auto-generated by compose-farm
# https://github.com/basnijholt/compose-farm
#
# This file routes traffic to stacks running on hosts other than Traefik's host.
# Services on Traefik's host use the Docker provider directly.
#
# Regenerate with: compose-farm traefik-file --all -o <this-file>
# Or configure traefik_file in compose-farm.yaml for automatic updates.

"""


def render_traefik_config(dynamic: dict[str, Any]) -> str:
    """Render Traefik dynamic config as YAML with a header comment."""
    body = yaml.safe_dump(dynamic, sort_keys=False)
    return _TRAEFIK_CONFIG_HEADER + body


_HOST_RULE_PATTERN = re.compile(r"Host\(`([^`]+)`\)")


def extract_website_urls(config: Config, stack: str) -> list[str]:
    """Extract website URLs from Traefik labels in a stack's compose file.

    Reuses generate_traefik_config to parse labels, then extracts Host() rules
    from router configurations.

    Returns a list of unique URLs, preferring HTTPS over HTTP.
    """
    try:
        dynamic, _ = generate_traefik_config(config, [stack], check_all=True)
    except FileNotFoundError:
        return []

    routers = dynamic.get("http", {}).get("routers", {})
    if not routers:
        return []

    # Track URLs with their scheme preference (https > http)
    urls: dict[str, str] = {}  # host -> scheme

    for router_info in routers.values():
        if not isinstance(router_info, dict):
            continue

        rule = router_info.get("rule", "")
        entrypoints = router_info.get("entrypoints", [])

        # entrypoints can be a list or string
        if isinstance(entrypoints, list):
            entrypoints_str = ",".join(entrypoints)
        else:
            entrypoints_str = str(entrypoints)

        # Determine scheme from entrypoint
        scheme = "https" if "websecure" in entrypoints_str else "http"

        # Extract host(s) from rule
        for match in _HOST_RULE_PATTERN.finditer(str(rule)):
            host = match.group(1)
            # Prefer https over http
            if host not in urls or scheme == "https":
                urls[host] = scheme

    # Build URL list, sorted for consistency
    return sorted(f"{scheme}://{host}" for host, scheme in urls.items())
