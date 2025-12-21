"""Fixtures for web UI tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from compose_farm.config import Config


@pytest.fixture
def compose_dir(tmp_path: Path) -> Path:
    """Create a temporary compose directory with sample stacks."""
    compose_path = tmp_path / "compose"
    compose_path.mkdir()

    # Create a sample stack
    plex_dir = compose_path / "plex"
    plex_dir.mkdir()
    (plex_dir / "compose.yaml").write_text("""
services:
  plex:
    image: plexinc/pms-docker
    container_name: plex
    ports:
      - "32400:32400"
""")
    (plex_dir / ".env").write_text("PLEX_CLAIM=claim-xxx\n")

    # Create another stack
    grafana_dir = compose_path / "grafana"
    grafana_dir.mkdir()
    (grafana_dir / "compose.yaml").write_text("""
services:
  grafana:
    image: grafana/grafana
""")

    # Create a single-service stack for testing service commands
    redis_dir = compose_path / "redis"
    redis_dir.mkdir()
    (redis_dir / "compose.yaml").write_text("""
services:
  redis:
    image: redis:alpine
""")

    return compose_path


@pytest.fixture
def config_file(tmp_path: Path, compose_dir: Path) -> Path:
    """Create a temporary config file and state file."""
    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text(f"""
compose_dir: {compose_dir}

hosts:
  server-1:
    address: 192.168.1.10
    user: docker
  server-2:
    address: 192.168.1.11

stacks:
  plex: server-1
  grafana: server-2
  redis: server-1
""")

    # State file must be alongside config file
    state_path = tmp_path / "compose-farm-state.yaml"
    state_path.write_text("""
deployed:
  plex: server-1
""")

    return config_path


@pytest.fixture
def mock_config(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Patch get_config to return a test config."""
    from compose_farm.config import load_config
    from compose_farm.web import deps as web_deps
    from compose_farm.web.routes import api as web_api

    config = load_config(config_file)

    # Patch in all modules that import get_config
    monkeypatch.setattr(web_deps, "get_config", lambda: config)
    monkeypatch.setattr(web_api, "get_config", lambda: config)

    return config
