"""Browser tests for HTMX behavior using Playwright.

Run with: nix-shell -p chromium --run "pytest tests/web/test_htmx_browser.py -v"
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uvicorn

if TYPE_CHECKING:
    from playwright.sync_api import Page


# Skip all tests if no browser available
pytestmark = pytest.mark.skipif(
    not shutil.which("chromium") and not shutil.which("google-chrome"),
    reason="No system browser available (run with: nix-shell -p chromium)",
)


@pytest.fixture(scope="session")
def browser_type_launch_args() -> dict[str, str]:
    """Configure Playwright to use system Chromium."""
    for name in ["chromium", "chromium-browser", "google-chrome", "chrome"]:
        path = shutil.which(name)
        if path:
            return {"executable_path": path}
    return {}


@pytest.fixture(scope="module")
def test_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create test config and compose files."""
    tmp: Path = tmp_path_factory.mktemp("data")

    # Create compose dir with services
    compose_dir = tmp / "compose"
    compose_dir.mkdir()
    for name in ["plex", "sonarr"]:
        svc = compose_dir / name
        svc.mkdir()
        (svc / "compose.yaml").write_text(f"services:\n  {name}:\n    image: test/{name}\n")

    # Create config
    config = tmp / "compose-farm.yaml"
    config.write_text(f"""
compose_dir: {compose_dir}
hosts:
  server-1:
    address: 192.168.1.10
    user: docker
services:
  plex: server-1
  sonarr: server-1
""")

    # Create state
    (tmp / "compose-farm-state.yaml").write_text("deployed:\n  plex: server-1\n")

    return config


@pytest.fixture(scope="module")
def server_url(
    test_config: Path, monkeypatch_module: pytest.MonkeyPatch
) -> Generator[str, None, None]:
    """Start test server and return URL."""
    import os
    import socket

    from compose_farm.config import load_config
    from compose_farm.web import deps as web_deps
    from compose_farm.web.app import create_app
    from compose_farm.web.routes import api as web_api
    from compose_farm.web.routes import pages as web_pages

    # Load the test config
    config = load_config(test_config)

    # Patch get_config in all modules that import it
    monkeypatch_module.setattr(web_deps, "get_config", lambda: config)
    monkeypatch_module.setattr(web_api, "get_config", lambda: config)
    monkeypatch_module.setattr(web_pages, "get_config", lambda: config)

    # Also set CF_CONFIG for any code that reads it directly
    os.environ["CF_CONFIG"] = str(test_config)

    # Find free port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    app = create_app()
    uvicorn_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(uvicorn_config)

    # Run in thread
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for startup
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            import urllib.request

            urllib.request.urlopen(url, timeout=0.5)  # noqa: S310
            break
        except Exception:
            time.sleep(0.1)

    yield url

    server.should_exit = True
    thread.join(timeout=2)

    # Clean up env
    os.environ.pop("CF_CONFIG", None)


@pytest.fixture(scope="module")
def monkeypatch_module() -> Generator[pytest.MonkeyPatch, None, None]:
    """Module-scoped monkeypatch."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


class TestDashboard:
    """Test dashboard page loads correctly."""

    def test_dashboard_loads(self, page: Page, server_url: str) -> None:
        """Dashboard page loads with all sections."""
        response = page.goto(server_url)
        assert response is not None, "No response from server"
        assert response.ok, f"Server returned {response.status}"

        page.wait_for_load_state("networkidle")

        # Check key elements exist
        page.wait_for_selector("#stats-cards", timeout=5000)
        page.wait_for_selector("#pending-operations", timeout=5000)

    def test_stats_cards_show_data(self, page: Page, server_url: str) -> None:
        """Stats cards display service counts."""
        page.goto(server_url)
        page.wait_for_selector("#stats-cards")

        content = page.locator("#stats-cards").inner_html()
        # Should show hosts, services, running, stopped counts
        assert "Hosts" in content or "1" in content
        assert "Services" in content or "2" in content

    def test_pending_operations_visible(self, page: Page, server_url: str) -> None:
        """Pending operations section shows sync status."""
        page.goto(server_url)
        page.wait_for_selector("#pending-operations")

        content = page.locator("#pending-operations").inner_html()
        # Should have either sync message or pending items
        assert len(content) > 0

    def test_save_button_exists(self, page: Page, server_url: str) -> None:
        """Save config button is present."""
        page.goto(server_url)
        save_btn = page.locator("#save-config-btn")
        assert save_btn.count() == 1


class TestSidebarNavigation:
    """Test sidebar navigation works."""

    def test_sidebar_has_service_links(self, page: Page, server_url: str) -> None:
        """Sidebar contains service navigation links."""
        page.goto(server_url)
        # Wait for sidebar to load via HTMX
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Check sidebar has service links
        service_links = page.locator("#sidebar-services a[href*='/service/']")
        assert service_links.count() >= 1

    def test_sidebar_navigation_to_service(self, page: Page, server_url: str) -> None:
        """Clicking service link navigates to service page."""
        page.goto(server_url)
        # Wait for sidebar to load via HTMX
        page.wait_for_selector("#sidebar-services a[href*='/service/']", timeout=5000)

        # Click first service link
        link = page.locator("#sidebar-services a[href*='/service/']").first
        service_name = link.get_attribute("href").split("/service/")[1]
        link.click()

        # Should navigate to service page
        page.wait_for_url(f"**/service/{service_name}", timeout=5000)
        assert f"/service/{service_name}" in page.url


class TestPartialEndpoints:
    """Test partial endpoints return valid HTML."""

    def test_stats_partial(self, page: Page, server_url: str) -> None:
        """Stats partial returns valid HTML."""
        response = page.goto(f"{server_url}/partials/stats")
        assert response.ok
        content = page.content()
        assert "stats-cards" in content

    def test_pending_partial(self, page: Page, server_url: str) -> None:
        """Pending partial returns valid HTML."""
        response = page.goto(f"{server_url}/partials/pending")
        assert response.ok
        content = page.content()
        assert "pending-operations" in content

    def test_sidebar_partial(self, page: Page, server_url: str) -> None:
        """Sidebar partial returns valid HTML."""
        response = page.goto(f"{server_url}/partials/sidebar")
        assert response.ok
        content = page.content()
        assert "href" in content  # Should have navigation links
