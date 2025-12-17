"""Configuration loading and Pydantic models."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from .paths import xdg_config_home


class Host(BaseModel):
    """SSH host configuration."""

    address: str
    user: str = Field(default_factory=getpass.getuser)
    port: int = 22


class Config(BaseModel):
    """Main configuration."""

    compose_dir: Path = Path("/opt/compose")
    hosts: dict[str, Host]
    services: dict[str, str | list[str]]  # service_name -> host_name or list of hosts
    traefik_file: Path | None = None  # Auto-regenerate traefik config after up/down
    traefik_service: str | None = None  # Service name for Traefik (skip its host in file-provider)
    config_path: Path = Path()  # Set by load_config()

    def get_state_path(self) -> Path:
        """Get the state file path (stored alongside config)."""
        return self.config_path.parent / "compose-farm-state.yaml"

    @model_validator(mode="after")
    def validate_hosts_and_services(self) -> Config:
        """Validate host names and service configurations."""
        # "all" is reserved keyword, cannot be used as host name
        if "all" in self.hosts:
            msg = "'all' is a reserved keyword and cannot be used as a host name"
            raise ValueError(msg)

        for service, host_value in self.services.items():
            # Validate list configurations
            if isinstance(host_value, list):
                if not host_value:
                    msg = f"Service '{service}' has empty host list"
                    raise ValueError(msg)
                if len(host_value) != len(set(host_value)):
                    msg = f"Service '{service}' has duplicate hosts in list"
                    raise ValueError(msg)

            # Validate all referenced hosts exist
            host_names = self.get_hosts(service)
            for host_name in host_names:
                if host_name not in self.hosts:
                    msg = f"Service '{service}' references unknown host '{host_name}'"
                    raise ValueError(msg)
        return self

    def get_hosts(self, service: str) -> list[str]:
        """Get list of host names for a service.

        Supports:
        - Single host: "truenas-debian" -> ["truenas-debian"]
        - All hosts: "all" -> list of all configured hosts
        - Explicit list: ["host1", "host2"] -> ["host1", "host2"]
        """
        if service not in self.services:
            msg = f"Unknown service: {service}"
            raise ValueError(msg)
        host_value = self.services[service]
        if isinstance(host_value, list):
            return host_value
        if host_value == "all":
            return list(self.hosts.keys())
        return [host_value]

    def is_multi_host(self, service: str) -> bool:
        """Check if a service runs on multiple hosts."""
        return len(self.get_hosts(service)) > 1

    def get_host(self, service: str) -> Host:
        """Get host config for a service (first host if multi-host)."""
        if service not in self.services:
            msg = f"Unknown service: {service}"
            raise ValueError(msg)
        host_names = self.get_hosts(service)
        return self.hosts[host_names[0]]

    def get_compose_path(self, service: str) -> Path:
        """Get compose file path for a service.

        Tries compose.yaml first, then docker-compose.yml.
        """
        service_dir = self.compose_dir / service
        for filename in (
            "compose.yaml",
            "compose.yml",
            "docker-compose.yml",
            "docker-compose.yaml",
        ):
            candidate = service_dir / filename
            if candidate.exists():
                return candidate
        # Default to compose.yaml if none exist (will error later)
        return service_dir / "compose.yaml"

    def discover_compose_dirs(self) -> set[str]:
        """Find all directories in compose_dir that contain a compose file."""
        compose_filenames = {
            "compose.yaml",
            "compose.yml",
            "docker-compose.yml",
            "docker-compose.yaml",
        }
        found: set[str] = set()
        if not self.compose_dir.exists():
            return found
        for subdir in self.compose_dir.iterdir():
            if subdir.is_dir():
                for filename in compose_filenames:
                    if (subdir / filename).exists():
                        found.add(subdir.name)
                        break
        return found


def _parse_hosts(raw_hosts: dict[str, str | dict[str, str | int]]) -> dict[str, Host]:
    """Parse hosts from config, handling both simple and full forms."""
    hosts = {}
    for name, value in raw_hosts.items():
        if isinstance(value, str):
            # Simple form: hostname: address
            hosts[name] = Host(address=value)
        else:
            # Full form: hostname: {address: ..., user: ..., port: ...}
            hosts[name] = Host(**value)
    return hosts


def load_config(path: Path | None = None) -> Config:
    """Load configuration from YAML file.

    Search order:
    1. Explicit path if provided via --config
    2. CF_CONFIG environment variable
    3. ./compose-farm.yaml
    4. $XDG_CONFIG_HOME/compose-farm/compose-farm.yaml (defaults to ~/.config)
    """
    search_paths = [
        Path("compose-farm.yaml"),
        xdg_config_home() / "compose-farm" / "compose-farm.yaml",
    ]

    if path:
        config_path = path
    elif env_path := os.environ.get("CF_CONFIG"):
        config_path = Path(env_path)
    else:
        config_path = None
        for p in search_paths:
            if p.exists():
                config_path = p
                break

    if config_path is None or not config_path.exists():
        msg = f"Config file not found. Searched: {', '.join(str(p) for p in search_paths)}"
        raise FileNotFoundError(msg)

    if config_path.is_dir():
        msg = (
            f"Config path is a directory, not a file: {config_path}\n"
            "This often happens when Docker creates an empty directory for a missing mount."
        )
        raise FileNotFoundError(msg)

    with config_path.open() as f:
        raw = yaml.safe_load(f)

    # Parse hosts with flexible format support
    raw["hosts"] = _parse_hosts(raw.get("hosts", {}))
    raw["config_path"] = config_path.resolve()

    return Config(**raw)
