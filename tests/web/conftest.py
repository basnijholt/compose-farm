"""Fixtures for web UI tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from compose_farm.config import Config


@pytest.fixture
def compose_dir(tmp_path: Path) -> Path:
    """Create a temporary compose directory with sample services."""
    compose_path = tmp_path / "compose"
    compose_path.mkdir()

    # Create a sample service
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

    # Create another service
    sonarr_dir = compose_path / "sonarr"
    sonarr_dir.mkdir()
    (sonarr_dir / "compose.yaml").write_text("""
services:
  sonarr:
    image: linuxserver/sonarr
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

services:
  plex: server-1
  sonarr: server-2
""")

    # State file must be alongside config file
    state_path = tmp_path / "compose-farm-state.yaml"
    state_path.write_text("""
deployed:
  plex: server-1
""")

    return config_path


@pytest.fixture
def mock_config(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Config, None, None]:
    """Patch get_config to return a test config."""
    from compose_farm.config import load_config
    from compose_farm.web import app as web_app
    from compose_farm.web.routes import api as web_api

    config = load_config(config_file)

    # Save original and clear cache before patching
    original_get_config = web_app.get_config
    original_get_config.cache_clear()

    # Patch in all modules that import get_config
    monkeypatch.setattr(web_app, "get_config", lambda: config)
    monkeypatch.setattr(web_api, "get_config", lambda: config)

    yield config

    # monkeypatch auto-restores, then clear cache
    # (cache_clear happens after monkeypatch cleanup via addfinalizier)
    monkeypatch.undo()
    original_get_config.cache_clear()


@pytest.fixture
def client(mock_config: Config) -> Generator[TestClient, None, None]:
    """Create a FastAPI test client with mocked config."""
    from compose_farm.web.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
