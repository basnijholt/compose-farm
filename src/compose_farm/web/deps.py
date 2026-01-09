"""Shared dependencies for web modules.

This module contains shared config and template accessors to avoid circular imports
between app.py and route modules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from compose_farm.executor import is_local

if TYPE_CHECKING:
    from compose_farm.config import Config, Host

# Paths
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def get_config() -> Config:
    """Load config from disk (always fresh)."""
    from compose_farm.config import load_config  # noqa: PLC0415

    return load_config()


def get_templates() -> Jinja2Templates:
    """Get Jinja2 templates instance."""
    return Jinja2Templates(directory=str(TEMPLATES_DIR))


def extract_config_error(exc: Exception) -> str:
    """Extract a user-friendly error message from a config exception."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            msg = err.get("msg", str(err))
            loc = err.get("loc", ())
            if loc:
                # Format location as dot-separated path (e.g., "hosts.nas.port")
                loc_str = ".".join(str(part) for part in loc)
                parts.append(f"{loc_str}: {msg}")
            else:
                parts.append(msg)
        return "; ".join(parts)
    return str(exc)


def _get_explicit_local_host(config: Config) -> str | None:
    """Get explicit local host from env var or config (env takes precedence)."""
    return os.environ.get("CF_LOCAL_HOST") or config.local_host


def is_local_host(host_name: str, host: Host, config: Config) -> bool:
    """Check if a host should be treated as local.

    When running in a Docker container, is_local() may not work correctly because
    the container has different network IPs. This function first checks if the
    host matches CF_LOCAL_HOST or config.local_host, then falls back to is_local().

    This affects:
    - Container exec (local docker exec vs SSH)
    - File read/write (local filesystem vs SSH)
    - Shell sessions (local shell vs SSH)
    """
    explicit_local = _get_explicit_local_host(config)
    if explicit_local and host_name == explicit_local:
        return True
    return is_local(host)


def get_local_host(config: Config) -> str | None:
    """Find the local host name from config, if any.

    First checks CF_LOCAL_HOST env var and config.local_host,
    then falls back to is_local() detection.
    """
    # Explicit setting takes precedence
    explicit = _get_explicit_local_host(config)
    if explicit and explicit in config.hosts:
        return explicit
    # Fall back to auto-detection
    for name, host in config.hosts.items():
        if is_local(host):
            return name
    return None
