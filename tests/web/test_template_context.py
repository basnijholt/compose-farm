"""Tests to verify template context variables match what templates expect.

Uses runtime validation by actually rendering templates and catching errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from compose_farm.config import Config


@pytest.fixture
def mock_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Create a minimal mock config for template testing."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()

    # Create minimal stack directory
    stack_dir = compose_dir / "test-service"
    stack_dir.mkdir()
    (stack_dir / "compose.yaml").write_text("services:\n  app:\n    image: nginx\n")

    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text(f"""
compose_dir: {compose_dir}
hosts:
  local-host:
    address: localhost
stacks:
  test-service: local-host
""")

    state_path = tmp_path / "compose-farm-state.yaml"
    state_path.write_text("deployed:\n  test-service: local-host\n")

    from compose_farm.config import load_config

    config = load_config(config_path)

    # Patch get_config in all relevant modules
    from compose_farm.web import deps
    from compose_farm.web.routes import api, pages

    monkeypatch.setattr(deps, "get_config", lambda: config)
    monkeypatch.setattr(api, "get_config", lambda: config)
    monkeypatch.setattr(pages, "get_config", lambda: config)

    return config


@pytest.fixture
def client(mock_config: Config) -> TestClient:
    """Create a test client with mocked config."""
    from compose_farm.web.app import create_app

    return TestClient(create_app())


class TestPageTemplatesRender:
    """Test that page templates render without missing variables."""

    def test_index_renders(self, client: TestClient) -> None:
        """Test index page renders without errors."""
        response = client.get("/")
        assert response.status_code == 200
        assert "Compose Farm" in response.text

    def test_console_renders(self, client: TestClient) -> None:
        """Test console page renders without errors."""
        response = client.get("/console")
        assert response.status_code == 200
        assert "Console" in response.text
        assert "Terminal" in response.text

    def test_stack_detail_renders(self, client: TestClient) -> None:
        """Test stack detail page renders without errors."""
        response = client.get("/stack/test-service")
        assert response.status_code == 200
        assert "test-service" in response.text

    def test_stack_detail_has_containers_data(self, client: TestClient) -> None:
        """Test stack detail page includes data-containers for command palette shell."""
        response = client.get("/stack/test-service")
        assert response.status_code == 200
        # Should have data-containers attribute with JSON
        assert "data-containers=" in response.text
        # Container name should follow {project}-{service}-1 pattern
        assert "test-service-app-1" in response.text


class TestPartialTemplatesRender:
    """Test that partial templates render without missing variables."""

    def test_sidebar_renders(self, client: TestClient) -> None:
        """Test sidebar partial renders without errors."""
        response = client.get("/partials/sidebar")
        assert response.status_code == 200
        assert "Dashboard" in response.text
        assert "Console" in response.text

    def test_stats_renders(self, client: TestClient) -> None:
        """Test stats partial renders without errors."""
        response = client.get("/partials/stats")
        assert response.status_code == 200

    def test_pending_renders(self, client: TestClient) -> None:
        """Test pending partial renders without errors."""
        response = client.get("/partials/pending")
        assert response.status_code == 200

    def test_stacks_by_host_renders(self, client: TestClient) -> None:
        """Test stacks_by_host partial renders without errors."""
        response = client.get("/partials/stacks-by-host")
        assert response.status_code == 200
