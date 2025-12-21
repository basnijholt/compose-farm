"""Path utilities - lightweight module with no heavy dependencies."""

from __future__ import annotations

import os
from pathlib import Path


def xdg_config_home() -> Path:
    """Get XDG config directory, respecting XDG_CONFIG_HOME env var."""
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def config_dir() -> Path:
    """Get the compose-farm config directory."""
    return xdg_config_home() / "compose-farm"


def default_config_path() -> Path:
    """Get the default user config path."""
    return config_dir() / "compose-farm.yaml"


def backup_dir() -> Path:
    """Get the backup directory for file edits."""
    return config_dir() / "backups"


def config_search_paths() -> list[Path]:
    """Get search paths for config files."""
    return [Path("compose-farm.yaml"), default_config_path()]


def find_config_path() -> Path | None:
    """Find the config file path, checking CF_CONFIG env var and search paths."""
    if env_path := os.environ.get("CF_CONFIG"):
        p = Path(env_path)
        if p.exists() and p.is_file():
            return p
    for p in config_search_paths():
        if p.exists() and p.is_file():
            return p
    return None
