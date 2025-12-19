"""Browser tests for HTMX behavior using Playwright.

Run with: nix-shell --run "uv run pytest tests/web/test_htmx_browser.py -v --no-cov"
Or on CI: playwright install chromium --with-deps
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
import urllib.request
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uvicorn

from compose_farm.config import load_config
from compose_farm.web import deps as web_deps
from compose_farm.web.app import create_app
from compose_farm.web.routes import api as web_api
from compose_farm.web.routes import pages as web_pages

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _browser_available() -> bool:
    """Check if any chromium browser is available (system or Playwright-managed)."""
    # Check for system browser
    if shutil.which("chromium") or shutil.which("google-chrome"):
        return True

    # Check for Playwright-managed browser
    try:
        from playwright._impl._driver import compute_driver_executable

        driver_path = compute_driver_executable()
        return Path(driver_path).exists()
    except Exception:
        return False


# Skip all tests if no browser available
pytestmark = pytest.mark.skipif(
    not _browser_available(),
    reason="No browser available (install via: playwright install chromium --with-deps)",
)


@pytest.fixture(scope="session")
def browser_type_launch_args() -> dict[str, str]:
    """Configure Playwright to use system Chromium if available, else use bundled."""
    # Prefer system browser if available (for nix-shell usage)
    for name in ["chromium", "chromium-browser", "google-chrome", "chrome"]:
        path = shutil.which(name)
        if path:
            return {"executable_path": path}
    # Fall back to Playwright's bundled browser (for CI)
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

    # Create state (plex running, sonarr not started)
    (tmp / "compose-farm-state.yaml").write_text("deployed:\n  plex: server-1\n")

    return config


@pytest.fixture(scope="module")
def server_url(
    test_config: Path, monkeypatch_module: pytest.MonkeyPatch
) -> Generator[str, None, None]:
    """Start test server and return URL."""
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


class TestHTMXSidebarLoading:
    """Test that sidebar loads dynamically via HTMX."""

    def test_sidebar_initially_shows_loading(self, page: Page, server_url: str) -> None:
        """Sidebar shows loading spinner before HTMX loads content."""
        # Intercept the sidebar request to delay it
        page.route("**/partials/sidebar", lambda route: route.abort())

        page.goto(server_url)

        # Before HTMX loads, should see loading indicator
        nav = page.locator("nav")
        assert "Loading" in nav.inner_text() or nav.locator(".loading").count() > 0

    def test_sidebar_loads_services_via_htmx(self, page: Page, server_url: str) -> None:
        """Sidebar fetches and displays services via hx-get on load."""
        page.goto(server_url)

        # Wait for HTMX to load sidebar content
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Verify actual services from test config appear
        services = page.locator("#sidebar-services li")
        assert services.count() == 2  # plex and sonarr

        # Check specific services are present
        content = page.locator("#sidebar-services").inner_text()
        assert "plex" in content
        assert "sonarr" in content

    def test_sidebar_shows_running_status(self, page: Page, server_url: str) -> None:
        """Sidebar shows running/stopped status indicators for services."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # plex is in state (running) - should have success status
        plex_item = page.locator("#sidebar-services li", has_text="plex")
        assert plex_item.locator(".status-success").count() == 1

        # sonarr is NOT in state (not started) - should have neutral status
        sonarr_item = page.locator("#sidebar-services li", has_text="sonarr")
        assert sonarr_item.locator(".status-neutral").count() == 1


class TestHTMXBoostNavigation:
    """Test hx-boost SPA-like navigation."""

    def test_navigation_updates_url_without_full_reload(self, page: Page, server_url: str) -> None:
        """Clicking boosted link updates URL without full page reload."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Add a marker to detect full page reload
        page.evaluate("window.__htmxTestMarker = 'still-here'")

        # Click a service link (boosted via hx-boost on parent)
        page.locator("#sidebar-services a", has_text="plex").click()

        # Wait for navigation
        page.wait_for_url("**/service/plex", timeout=5000)

        # Verify URL changed
        assert "/service/plex" in page.url

        # Verify NO full page reload (marker should still exist)
        marker = page.evaluate("window.__htmxTestMarker")
        assert marker == "still-here", "Full page reload occurred - hx-boost not working"

    def test_main_content_replaced_on_navigation(self, page: Page, server_url: str) -> None:
        """Navigation replaces #main-content via hx-target/hx-select."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Get initial main content
        initial_content = page.locator("#main-content").inner_text()
        assert "Compose Farm" in initial_content  # Dashboard title

        # Navigate to service page
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Main content should now show service page
        new_content = page.locator("#main-content").inner_text()
        assert "plex" in new_content.lower()
        assert "Compose Farm" not in new_content  # Dashboard title should be gone


class TestDashboardContent:
    """Test dashboard displays correct data."""

    def test_stats_show_correct_counts(self, page: Page, server_url: str) -> None:
        """Stats cards show accurate host/service counts from config."""
        page.goto(server_url)
        page.wait_for_selector("#stats-cards", timeout=5000)

        stats = page.locator("#stats-cards").inner_text()

        # From test config: 1 host, 2 services, 1 running (plex), 1 stopped (sonarr)
        assert "1" in stats  # hosts count
        assert "2" in stats  # services count

    def test_pending_shows_not_started_service(self, page: Page, server_url: str) -> None:
        """Pending operations shows sonarr as not started."""
        page.goto(server_url)
        page.wait_for_selector("#pending-operations", timeout=5000)

        pending = page.locator("#pending-operations")
        content = pending.inner_text()

        # sonarr is not in state, should show as not started
        assert "sonarr" in content.lower() or "Not Started" in content


class TestSaveConfigButton:
    """Test save config button behavior."""

    def test_save_button_shows_saved_feedback(self, page: Page, server_url: str) -> None:
        """Clicking save shows 'Saved!' feedback text."""
        page.goto(server_url)
        page.wait_for_selector("#save-config-btn", timeout=5000)

        save_btn = page.locator("#save-config-btn")
        initial_text = save_btn.inner_text()
        assert "Save" in initial_text

        # Click save
        save_btn.click()

        # Wait for feedback
        page.wait_for_function(
            "document.querySelector('#save-config-btn')?.textContent?.includes('Saved')",
            timeout=5000,
        )

        # Verify feedback shown
        assert "Saved" in save_btn.inner_text()


class TestServiceDetailPage:
    """Test service detail page via HTMX navigation."""

    def test_service_page_shows_service_info(self, page: Page, server_url: str) -> None:
        """Service page displays service information."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Navigate to plex service
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Should show service name and host info
        content = page.locator("#main-content").inner_text()
        assert "plex" in content.lower()
        assert "server-1" in content  # assigned host from config
        # Should show compose file path
        assert "compose.yaml" in content

    def test_back_navigation_works(self, page: Page, server_url: str) -> None:
        """Browser back button works after HTMX navigation."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Navigate to service
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Go back
        page.go_back()
        page.wait_for_url(server_url, timeout=5000)

        # Should be back on dashboard
        assert page.url.rstrip("/") == server_url.rstrip("/")
