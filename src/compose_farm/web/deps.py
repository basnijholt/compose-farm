"""Shared dependencies for web modules.

This module contains shared config and template accessors to avoid circular imports
between app.py and route modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from compose_farm.config import Config

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
        return "; ".join(err.get("msg", str(err)) for err in exc.errors())
    return str(exc)


def get_running_services_by_host(
    state: dict[str, str | list[str]],
    hosts: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Group running services by host, filtering out hosts with no services.

    This is a convenience wrapper around group_services_by_host that filters
    out empty entries.
    """
    from compose_farm.state import group_services_by_host  # noqa: PLC0415

    by_host = group_services_by_host(state, hosts)
    return {h: svcs for h, svcs in by_host.items() if svcs}
