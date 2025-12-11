"""Configuration loading and Pydantic models."""

from __future__ import annotations

import getpass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class Host(BaseModel):
    """SSH host configuration."""

    address: str
    user: str = Field(default_factory=getpass.getuser)
    port: int = 22


class Config(BaseModel):
    """Main configuration."""

    compose_dir: Path = Path("/opt/compose")
    hosts: dict[str, Host]
    services: dict[str, str]  # service_name -> host_name

    @model_validator(mode="after")
    def validate_service_hosts(self) -> Config:
        """Ensure all services reference valid hosts."""
        for service, host_name in self.services.items():
            if host_name not in self.hosts:
                msg = f"Service '{service}' references unknown host '{host_name}'"
                raise ValueError(msg)
        return self

    def get_host(self, service: str) -> Host:
        """Get host config for a service."""
        if service not in self.services:
            msg = f"Unknown service: {service}"
            raise ValueError(msg)
        return self.hosts[self.services[service]]

    def get_compose_path(self, service: str) -> Path:
        """Get compose file path for a service."""
        return self.compose_dir / service / "docker-compose.yml"


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
    1. Explicit path if provided
    2. ./compose-farm.yaml
    3. ~/.config/compose-farm/compose-farm.yaml
    """
    search_paths = [
        Path("compose-farm.yaml"),
        Path.home() / ".config" / "compose-farm" / "compose-farm.yaml",
    ]

    if path:
        config_path = path
    else:
        config_path = None
        for p in search_paths:
            if p.exists():
                config_path = p
                break

    if config_path is None or not config_path.exists():
        msg = f"Config file not found. Searched: {', '.join(str(p) for p in search_paths)}"
        raise FileNotFoundError(msg)

    with config_path.open() as f:
        raw = yaml.safe_load(f)

    # Parse hosts with flexible format support
    raw["hosts"] = _parse_hosts(raw.get("hosts", {}))

    return Config(**raw)
