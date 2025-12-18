"""Shared dependencies for web modules.

This module contains shared config and template accessors to avoid circular imports
between app.py and route modules.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from compose_farm.config import Config

# Paths
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


@lru_cache
def get_config() -> Config:
    """Load config once per process (cached)."""
    from compose_farm.config import load_config  # noqa: PLC0415

    return load_config()


def reload_config() -> Config:
    """Clear config cache and reload from disk."""
    get_config.cache_clear()
    return get_config()


def get_templates() -> Jinja2Templates:
    """Get Jinja2 templates instance."""
    return Jinja2Templates(directory=str(TEMPLATES_DIR))
