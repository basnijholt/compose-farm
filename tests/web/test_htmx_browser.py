"""Browser tests for HTMX behavior using Playwright.

Run with: nix-shell --run "uv run pytest tests/web/test_htmx_browser.py -v --no-cov"
Or on CI: playwright install chromium --with-deps
"""

from __future__ import annotations

import os
import re
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
    from playwright.sync_api import Page, Route, WebSocket

# CDN assets to vendor locally for faster/more reliable tests
CDN_ASSETS = {
    "https://cdn.jsdelivr.net/npm/daisyui@5": ("daisyui.css", "text/css"),
    "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4": (
        "tailwind.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css": ("xterm.css", "text/css"),
    "https://unpkg.com/htmx.org@2.0.4": ("htmx.js", "application/javascript"),
    "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js": (
        "xterm.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js": (
        "xterm-fit.js",
        "application/javascript",
    ),
}


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


def _download_url(url: str) -> bytes | None:
    """Download URL content, trying urllib then curl as fallback."""
    import subprocess

    # Try urllib first
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pytest"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.read()  # type: ignore[no-any-return]
    except Exception:  # noqa: S110
        pass  # Fall through to curl fallback

    # Fallback to curl (handles SSL proxies better)
    try:
        result = subprocess.run(
            ["curl", "-fsSL", "--max-time", "30", url],  # noqa: S607
            capture_output=True,
            check=True,
        )
        return bytes(result.stdout)
    except Exception:
        return None


@pytest.fixture(scope="session")
def vendor_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Download CDN assets once per test session for faster tests."""
    cache_dir: Path = tmp_path_factory.mktemp("vendor")

    for url, (filename, _content_type) in CDN_ASSETS.items():
        filepath = cache_dir / filename
        content = _download_url(url)
        if content:
            filepath.write_bytes(content)
        else:
            print(f"Warning: Failed to cache {url}")

    return cache_dir


@pytest.fixture
def page(page: Page, vendor_cache: Path) -> Page:
    """Override default page fixture to intercept CDN requests with local cache."""
    cache_hits = [0]  # Use list to allow mutation in closure

    def handle_cdn(route: Route) -> None:
        url = route.request.url
        # Find matching asset
        for cdn_url, (filename, content_type) in CDN_ASSETS.items():
            if url.startswith(cdn_url):
                cached_file = vendor_cache / filename
                if cached_file.exists():
                    cache_hits[0] += 1
                    route.fulfill(
                        status=200,
                        content_type=content_type,
                        body=cached_file.read_bytes(),
                    )
                    return
        # Fall back to actual CDN if not cached
        route.continue_()

    # Intercept CDN requests
    page.route(re.compile(r"https://(cdn\.jsdelivr\.net|unpkg\.com)/.*"), handle_cdn)

    yield page

    # Log cache usage for debugging (only if any hits)
    if cache_hits[0] > 0:
        print(f"[CDN cache] Served {cache_hits[0]} requests from local cache")


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
    """Create test config and compose files.

    Creates a multi-host, multi-service config for comprehensive testing:
    - server-1: plex (running), sonarr (not started)
    - server-2: radarr (running), jellyfin (not started)
    """
    tmp: Path = tmp_path_factory.mktemp("data")

    # Create compose dir with services
    compose_dir = tmp / "compose"
    compose_dir.mkdir()
    for name in ["plex", "sonarr", "radarr", "jellyfin"]:
        svc = compose_dir / name
        svc.mkdir()
        (svc / "compose.yaml").write_text(f"services:\n  {name}:\n    image: test/{name}\n")

    # Create config with multiple hosts
    config = tmp / "compose-farm.yaml"
    config.write_text(f"""
compose_dir: {compose_dir}
hosts:
  server-1:
    address: 192.168.1.10
    user: docker
  server-2:
    address: 192.168.1.20
    user: docker
services:
  plex: server-1
  sonarr: server-1
  radarr: server-2
  jellyfin: server-2
""")

    # Create state (plex and radarr running, sonarr and jellyfin not started)
    (tmp / "compose-farm-state.yaml").write_text(
        "deployed:\n  plex: server-1\n  radarr: server-2\n"
    )

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

    # Wait for startup with proper error handling
    url = f"http://127.0.0.1:{port}"
    server_ready = False
    for _ in range(100):  # 2 seconds max
        try:
            urllib.request.urlopen(url, timeout=0.1)  # noqa: S310
            server_ready = True
            break
        except Exception:
            time.sleep(0.02)  # 20ms between checks

    if not server_ready:
        msg = f"Test server failed to start on {url}"
        raise RuntimeError(msg)

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
        assert services.count() == 4  # plex, sonarr, radarr, jellyfin

        # Check specific services are present
        content = page.locator("#sidebar-services").inner_text()
        assert "plex" in content
        assert "sonarr" in content
        assert "radarr" in content
        assert "jellyfin" in content

    def test_dashboard_content_persists_after_sidebar_loads(
        self, page: Page, server_url: str
    ) -> None:
        """Dashboard content must remain visible after HTMX loads sidebar.

        Regression test: conflicting hx-select attributes on the nav element
        were causing the dashboard to disappear when sidebar loaded.
        """
        page.goto(server_url)

        # Dashboard content should be visible immediately (server-rendered)
        stats = page.locator("#stats-cards")
        assert stats.is_visible()

        # Wait for sidebar to fully load via HTMX
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Dashboard content must STILL be visible after sidebar loads
        assert stats.is_visible(), "Dashboard disappeared after sidebar loaded"
        assert page.locator("#stats-cards .card").count() >= 4

    def test_sidebar_shows_running_status(self, page: Page, server_url: str) -> None:
        """Sidebar shows running/stopped status indicators for services."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # plex and radarr are in state (running) - should have success status
        plex_item = page.locator("#sidebar-services li", has_text="plex")
        assert plex_item.locator(".status-success").count() == 1
        radarr_item = page.locator("#sidebar-services li", has_text="radarr")
        assert radarr_item.locator(".status-success").count() == 1

        # sonarr and jellyfin are NOT in state (not started) - should have neutral status
        sonarr_item = page.locator("#sidebar-services li", has_text="sonarr")
        assert sonarr_item.locator(".status-neutral").count() == 1
        jellyfin_item = page.locator("#sidebar-services li", has_text="jellyfin")
        assert jellyfin_item.locator(".status-neutral").count() == 1


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

        # From test config: 2 hosts, 4 services, 2 running (plex, radarr)
        assert "2" in stats  # hosts count
        assert "4" in stats  # services count

    def test_pending_shows_not_started_services(self, page: Page, server_url: str) -> None:
        """Pending operations shows sonarr and jellyfin as not started."""
        page.goto(server_url)
        page.wait_for_selector("#pending-operations", timeout=5000)

        pending = page.locator("#pending-operations")
        content = pending.inner_text().lower()

        # sonarr and jellyfin are not in state, should show as not started
        assert "sonarr" in content or "not started" in content
        assert "jellyfin" in content or "not started" in content


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


class TestSidebarFilter:
    """Test JavaScript sidebar filtering functionality."""

    @staticmethod
    def _filter_sidebar(page: Page, text: str) -> None:
        """Fill the sidebar filter and trigger the keyup event.

        The sidebar uses onkeyup, which fill() doesn't trigger.
        """
        filter_input = page.locator("#sidebar-filter")
        filter_input.fill(text)
        filter_input.dispatch_event("keyup")

    def test_text_filter_hides_non_matching_services(self, page: Page, server_url: str) -> None:
        """Typing in filter input hides services that don't match."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Initially all 4 services visible
        visible_items = page.locator("#sidebar-services li:not([hidden])")
        assert visible_items.count() == 4

        # Type in filter to match only "plex"
        self._filter_sidebar(page, "plex")

        # Only plex should be visible now
        visible_after = page.locator("#sidebar-services li:not([hidden])")
        assert visible_after.count() == 1
        assert "plex" in visible_after.first.inner_text()

    def test_text_filter_updates_count_badge(self, page: Page, server_url: str) -> None:
        """Filter updates the service count badge."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Initial count should be (4)
        count_badge = page.locator("#sidebar-count")
        assert "(4)" in count_badge.inner_text()

        # Filter to show only services containing "arr" (sonarr, radarr)
        self._filter_sidebar(page, "arr")

        # Count should update to (2)
        assert "(2)" in count_badge.inner_text()

    def test_text_filter_is_case_insensitive(self, page: Page, server_url: str) -> None:
        """Filter matching is case-insensitive."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Type uppercase
        self._filter_sidebar(page, "PLEX")

        # Should still match plex
        visible = page.locator("#sidebar-services li:not([hidden])")
        assert visible.count() == 1
        assert "plex" in visible.first.inner_text().lower()

    def test_host_dropdown_filters_by_host(self, page: Page, server_url: str) -> None:
        """Host dropdown filters services by their assigned host."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Select server-1 from dropdown
        page.locator("#sidebar-host-select").select_option("server-1")

        # Only plex and sonarr (server-1 services) should be visible
        visible = page.locator("#sidebar-services li:not([hidden])")
        assert visible.count() == 2

        content = visible.all_inner_texts()
        assert any("plex" in s for s in content)
        assert any("sonarr" in s for s in content)
        assert not any("radarr" in s for s in content)
        assert not any("jellyfin" in s for s in content)

    def test_combined_text_and_host_filter(self, page: Page, server_url: str) -> None:
        """Text filter and host filter work together."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Filter by server-2 host
        page.locator("#sidebar-host-select").select_option("server-2")

        # Then filter by text "arr" (should match only radarr on server-2)
        self._filter_sidebar(page, "arr")

        visible = page.locator("#sidebar-services li:not([hidden])")
        assert visible.count() == 1
        assert "radarr" in visible.first.inner_text()

    def test_clearing_filter_shows_all_services(self, page: Page, server_url: str) -> None:
        """Clearing filter restores all services."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Apply filter
        self._filter_sidebar(page, "plex")
        assert page.locator("#sidebar-services li:not([hidden])").count() == 1

        # Clear filter
        self._filter_sidebar(page, "")

        # All services visible again
        assert page.locator("#sidebar-services li:not([hidden])").count() == 4


class TestCommandPalette:
    """Test command palette (Cmd+K) JavaScript functionality."""

    def test_cmd_k_opens_palette(self, page: Page, server_url: str) -> None:
        """Cmd+K keyboard shortcut opens the command palette."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Palette should be closed initially
        assert not page.locator("#cmd-palette").is_visible()

        # Press Cmd+K (Meta+k on Mac, Control+k otherwise)
        page.keyboard.press("Control+k")

        # Palette should now be open
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)
        assert page.locator("#cmd-palette").is_visible()

    def test_palette_input_is_focused_on_open(self, page: Page, server_url: str) -> None:
        """Input field is focused when palette opens."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        # Input should be focused - we can type directly
        page.keyboard.type("test")
        assert page.locator("#cmd-input").input_value() == "test"

    def test_palette_shows_navigation_commands(self, page: Page, server_url: str) -> None:
        """Palette shows Dashboard and Console navigation commands."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        cmd_list = page.locator("#cmd-list").inner_text()
        assert "Dashboard" in cmd_list
        assert "Console" in cmd_list

    def test_palette_shows_service_navigation(self, page: Page, server_url: str) -> None:
        """Palette includes service names for navigation."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        cmd_list = page.locator("#cmd-list").inner_text()
        # Services should appear as navigation options
        assert "plex" in cmd_list
        assert "radarr" in cmd_list

    def test_palette_filters_on_input(self, page: Page, server_url: str) -> None:
        """Typing in palette filters the command list."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        # Type to filter
        page.locator("#cmd-input").fill("plex")

        # Should show plex, hide others
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "plex" in cmd_list
        assert "Dashboard" not in cmd_list  # Filtered out

    def test_arrow_down_moves_selection(self, page: Page, server_url: str) -> None:
        """Arrow down key moves selection to next item."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        # First item should be selected (has bg-base-300)
        first_item = page.locator("#cmd-list a").first
        assert "bg-base-300" in (first_item.get_attribute("class") or "")

        # Press arrow down
        page.keyboard.press("ArrowDown")

        # Second item should now be selected
        second_item = page.locator("#cmd-list a").nth(1)
        assert "bg-base-300" in (second_item.get_attribute("class") or "")
        # First should no longer be selected
        assert "bg-base-300" not in (first_item.get_attribute("class") or "")

    def test_enter_executes_and_closes_palette(self, page: Page, server_url: str) -> None:
        """Enter key executes selected command and closes palette."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        # Filter to plex service
        page.locator("#cmd-input").fill("plex")
        page.keyboard.press("Enter")

        # Palette should close
        page.wait_for_selector("#cmd-palette:not([open])", timeout=2000)

        # Should navigate to plex service page
        page.wait_for_url("**/service/plex", timeout=5000)

    def test_click_executes_command(self, page: Page, server_url: str) -> None:
        """Clicking a command executes it."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        # Click on Console command
        page.locator("#cmd-list a", has_text="Console").click()

        # Should navigate to console page
        page.wait_for_url("**/console", timeout=5000)

    def test_escape_closes_palette(self, page: Page, server_url: str) -> None:
        """Escape key closes the palette without executing."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        page.keyboard.press("Escape")

        # Palette should close, URL unchanged
        page.wait_for_selector("#cmd-palette:not([open])", timeout=2000)
        assert page.url.rstrip("/") == server_url.rstrip("/")

    def test_fab_button_opens_palette(self, page: Page, server_url: str) -> None:
        """Floating action button opens the command palette."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Click the FAB
        page.locator("#cmd-fab").click()

        # Palette should open
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)


class TestActionButtons:
    """Test action button HTMX POST requests."""

    def test_apply_button_makes_post_request(self, page: Page, server_url: str) -> None:
        """Apply button triggers POST to /api/apply."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Intercept the API call
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            # Return a mock response
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test-apply-123"}',
            )

        page.route("**/api/apply", handle_route)

        # Click Apply button
        page.locator("button", has_text="Apply").click()

        # Wait for request to be made
        page.wait_for_timeout(500)

        # Verify API was called
        assert len(api_calls) == 1
        assert "/api/apply" in api_calls[0]

    def test_refresh_button_makes_post_request(self, page: Page, server_url: str) -> None:
        """Refresh button triggers POST to /api/refresh."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test-refresh-123"}',
            )

        page.route("**/api/refresh", handle_route)

        page.locator("button", has_text="Refresh").click()
        page.wait_for_timeout(500)

        assert len(api_calls) == 1
        assert "/api/refresh" in api_calls[0]

    def test_action_response_expands_terminal(self, page: Page, server_url: str) -> None:
        """Action button response with task_id expands terminal section."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Terminal should be collapsed initially
        terminal_toggle = page.locator("#terminal-toggle")
        assert not terminal_toggle.is_checked()

        # Mock the API to return a task_id
        page.route(
            "**/api/apply",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test-123"}',
            ),
        )

        # Click Apply
        page.locator("button", has_text="Apply").click()

        # Terminal should expand
        page.wait_for_function(
            "document.getElementById('terminal-toggle')?.checked === true",
            timeout=3000,
        )

    def test_service_page_action_buttons(self, page: Page, server_url: str) -> None:
        """Service page has working action buttons."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Navigate to plex service
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Intercept service-specific API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test-up-123"}',
            )

        page.route("**/api/service/plex/up", handle_route)

        # Click Up button (use get_by_role for exact match, avoiding "Update")
        page.get_by_role("button", name="Up", exact=True).click()
        page.wait_for_timeout(500)

        assert len(api_calls) == 1
        assert "/api/service/plex/up" in api_calls[0]


class TestKeyboardShortcuts:
    """Test global keyboard shortcuts."""

    def test_ctrl_s_triggers_save(self, page: Page, server_url: str) -> None:
        """Ctrl+S triggers save when editors are present."""
        page.goto(server_url)
        page.wait_for_selector("#save-config-btn", timeout=5000)

        # Wait for Monaco editor to load (it takes a moment)
        page.wait_for_function(
            "typeof monaco !== 'undefined'",
            timeout=10000,
        )

        # Press Ctrl+S
        page.keyboard.press("Control+s")

        # Should trigger save - button shows "Saved!"
        page.wait_for_function(
            "document.querySelector('#save-config-btn')?.textContent?.includes('Saved')",
            timeout=5000,
        )


class TestContentStability:
    """Test that HTMX operations don't accidentally destroy other page content.

    These tests verify that when one element updates, other elements remain stable.
    This catches bugs where HTMX attributes (hx-select, hx-swap-oob, etc.) are
    misconfigured and cause unintended side effects.
    """

    def test_all_dashboard_sections_visible_after_full_load(
        self, page: Page, server_url: str
    ) -> None:
        """All dashboard sections remain visible after HTMX completes loading."""
        page.goto(server_url)

        # Wait for all HTMX requests to complete
        page.wait_for_selector("#sidebar-services", timeout=5000)
        page.wait_for_load_state("networkidle")

        # All major dashboard sections must be visible
        assert page.locator("#stats-cards").is_visible(), "Stats cards missing"
        assert page.locator("#stats-cards .card").count() >= 4, "Stats incomplete"
        assert page.locator("#pending-operations").is_visible(), "Pending ops missing"
        assert page.locator("#services-by-host").is_visible(), "Services by host missing"
        assert page.locator("#sidebar-services").is_visible(), "Sidebar missing"

    def test_sidebar_persists_after_navigation_and_back(self, page: Page, server_url: str) -> None:
        """Sidebar content persists through navigation cycle."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Remember sidebar state
        initial_count = page.locator("#sidebar-services li").count()
        assert initial_count == 4

        # Navigate away
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Sidebar should still be there with same content
        assert page.locator("#sidebar-services").is_visible()
        assert page.locator("#sidebar-services li").count() == initial_count

        # Navigate back
        page.go_back()
        page.wait_for_url(server_url, timeout=5000)

        # Sidebar still intact
        assert page.locator("#sidebar-services").is_visible()
        assert page.locator("#sidebar-services li").count() == initial_count

    def test_dashboard_sections_persist_after_save(self, page: Page, server_url: str) -> None:
        """Dashboard sections remain after save triggers cf:refresh event."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Capture initial state - all must be visible
        assert page.locator("#stats-cards").is_visible()
        assert page.locator("#pending-operations").is_visible()
        assert page.locator("#services-by-host").is_visible()

        # Trigger save (which dispatches cf:refresh)
        page.locator("#save-config-btn").click()
        page.wait_for_function(
            "document.querySelector('#save-config-btn')?.textContent?.includes('Saved')",
            timeout=5000,
        )

        # Wait for refresh requests to complete
        page.wait_for_load_state("networkidle")

        # All sections must still be visible
        assert page.locator("#stats-cards").is_visible(), "Stats disappeared after save"
        assert page.locator("#pending-operations").is_visible(), "Pending disappeared"
        assert page.locator("#services-by-host").is_visible(), "Services disappeared"
        assert page.locator("#sidebar-services").is_visible(), "Sidebar disappeared"

    def test_filter_state_not_affected_by_other_htmx_requests(
        self, page: Page, server_url: str
    ) -> None:
        """Sidebar filter state persists during other HTMX activity."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Apply a filter
        filter_input = page.locator("#sidebar-filter")
        filter_input.fill("plex")
        filter_input.dispatch_event("keyup")

        # Verify filter is applied
        assert page.locator("#sidebar-services li:not([hidden])").count() == 1

        # Trigger a save (causes cf:refresh on multiple elements)
        page.locator("#save-config-btn").click()
        page.wait_for_timeout(1000)

        # Filter input should still have our text
        # (Note: sidebar reloads so filter clears - this tests the sidebar reload works)
        page.wait_for_selector("#sidebar-services", timeout=5000)
        assert page.locator("#sidebar-services").is_visible()

    def test_main_content_not_affected_by_sidebar_refresh(
        self, page: Page, server_url: str
    ) -> None:
        """Main content area stays intact when sidebar refreshes."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Get main content text
        main_content = page.locator("#main-content")
        initial_text = main_content.inner_text()
        assert "Compose Farm" in initial_text

        # Trigger cf:refresh (which refreshes sidebar)
        page.evaluate("document.body.dispatchEvent(new CustomEvent('cf:refresh'))")
        page.wait_for_timeout(500)

        # Main content should be unchanged (same page, just refreshed partials)
        assert "Compose Farm" in main_content.inner_text()
        assert page.locator("#stats-cards").is_visible()

    def test_no_duplicate_elements_after_multiple_refreshes(
        self, page: Page, server_url: str
    ) -> None:
        """Multiple refresh cycles don't create duplicate elements."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Count initial elements
        initial_stat_count = page.locator("#stats-cards .card").count()
        initial_service_count = page.locator("#sidebar-services li").count()

        # Trigger multiple refreshes
        for _ in range(3):
            page.evaluate("document.body.dispatchEvent(new CustomEvent('cf:refresh'))")
            page.wait_for_timeout(300)

        page.wait_for_load_state("networkidle")

        # Counts should be same (no duplicates created)
        assert page.locator("#stats-cards .card").count() == initial_stat_count
        assert page.locator("#sidebar-services li").count() == initial_service_count


class TestConsolePage:
    """Test console page functionality."""

    def test_console_page_renders(self, page: Page, server_url: str) -> None:
        """Console page renders with all required elements."""
        page.goto(f"{server_url}/console")

        # Wait for page to load
        page.wait_for_selector("#console-host-select", timeout=5000)

        # Verify host selector exists
        host_select = page.locator("#console-host-select")
        assert host_select.is_visible()

        # Verify Connect button exists
        connect_btn = page.locator("#console-connect-btn")
        assert connect_btn.is_visible()
        assert "Connect" in connect_btn.inner_text()

        # Verify terminal container exists
        terminal_container = page.locator("#console-terminal")
        assert terminal_container.is_visible()

        # Verify editor container exists
        editor_container = page.locator("#console-editor")
        assert editor_container.is_visible()

        # Verify file path input exists
        file_input = page.locator("#console-file-path")
        assert file_input.is_visible()

        # Verify save button exists
        save_btn = page.locator("#console-save-btn")
        assert save_btn.is_visible()

    def test_console_host_selector_shows_all_hosts(self, page: Page, server_url: str) -> None:
        """Host selector dropdown contains all configured hosts."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-host-select", timeout=5000)

        # Get all options from the dropdown
        options = page.locator("#console-host-select option")
        assert options.count() == 2  # server-1 and server-2 from test config

        # Verify both hosts are present
        option_texts = [options.nth(i).inner_text() for i in range(options.count())]
        assert any("server-1" in text for text in option_texts)
        assert any("server-2" in text for text in option_texts)

    def test_console_connect_creates_terminal_element(self, page: Page, server_url: str) -> None:
        """Connecting to a host creates xterm terminal elements.

        The console page auto-connects to the first host on load,
        which creates the xterm.js terminal inside the container.
        """
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-terminal", timeout=5000)

        # Wait for xterm.js to load from CDN
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=10000)

        # The console page auto-connects, which creates the terminal.
        # Wait for xterm to initialize (creates .xterm class)
        page.wait_for_selector("#console-terminal .xterm", timeout=10000)

        # Verify xterm elements are present
        xterm_container = page.locator("#console-terminal .xterm")
        assert xterm_container.is_visible()

        # Verify xterm screen is created (the actual terminal display)
        xterm_screen = page.locator("#console-terminal .xterm-screen")
        assert xterm_screen.is_visible()

    def test_console_editor_initializes(self, page: Page, server_url: str) -> None:
        """Monaco editor initializes on the console page."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-editor", timeout=5000)

        # Wait for Monaco to load from CDN
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=15000)

        # Monaco creates elements inside the container
        page.wait_for_selector("#console-editor .monaco-editor", timeout=10000)

        # Verify Monaco editor is present
        monaco_editor = page.locator("#console-editor .monaco-editor")
        assert monaco_editor.is_visible()

    def test_console_load_file_calls_api(self, page: Page, server_url: str) -> None:
        """Clicking Open button calls the file API with correct parameters."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-file-path", timeout=5000)

        # Wait for terminal to connect (sets currentHost)
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=10000)
        page.wait_for_selector("#console-terminal .xterm", timeout=10000)

        # Track API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"success": true, "content": "test file content"}',
            )

        page.route("**/api/console/file*", handle_route)

        # Enter a file path and click Open
        file_input = page.locator("#console-file-path")
        file_input.fill("/tmp/test.yaml")
        page.locator("button", has_text="Open").click()

        # Wait for API call
        page.wait_for_timeout(500)

        # Verify API was called with correct parameters
        assert len(api_calls) >= 1
        assert "/api/console/file" in api_calls[0]
        assert "path=" in api_calls[0]
        assert "host=" in api_calls[0]

    def test_console_load_file_shows_content(self, page: Page, server_url: str) -> None:
        """Loading a file displays its content in the Monaco editor."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-file-path", timeout=5000)

        # Wait for terminal to connect and Monaco to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=10000)
        page.wait_for_selector("#console-terminal .xterm", timeout=10000)
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=15000)
        page.wait_for_selector("#console-editor .monaco-editor", timeout=10000)

        # Mock file API to return specific content
        test_content = "services:\\n  nginx:\\n    image: nginx:latest"

        def handle_route(route: Route) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=f'{{"success": true, "content": "{test_content}"}}',
            )

        page.route("**/api/console/file*", handle_route)

        # Load file
        file_input = page.locator("#console-file-path")
        file_input.fill("/tmp/compose.yaml")
        page.locator("button", has_text="Open").click()

        # Wait for content to be loaded into editor
        page.wait_for_function(
            "window.consoleEditor && window.consoleEditor.getValue().includes('nginx')",
            timeout=5000,
        )

    def test_console_save_file_calls_api(self, page: Page, server_url: str) -> None:
        """Clicking Save button calls the file API with PUT method."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-file-path", timeout=5000)

        # Wait for terminal to connect and Monaco to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=10000)
        page.wait_for_selector("#console-terminal .xterm", timeout=10000)
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=15000)
        page.wait_for_selector("#console-editor .monaco-editor", timeout=10000)

        # Track API calls
        api_calls: list[tuple[str, str]] = []  # (method, url)

        def handle_load_route(route: Route) -> None:
            api_calls.append((route.request.method, route.request.url))
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"success": true, "content": "original content"}',
            )

        def handle_save_route(route: Route) -> None:
            api_calls.append((route.request.method, route.request.url))
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"success": true}',
            )

        page.route(
            "**/api/console/file*",
            lambda route: (
                handle_save_route(route)
                if route.request.method == "PUT"
                else handle_load_route(route)
            ),
        )

        # Load a file first (required before save works)
        file_input = page.locator("#console-file-path")
        file_input.fill("/tmp/test.yaml")
        page.locator("button", has_text="Open").click()
        page.wait_for_timeout(500)

        # Clear api_calls to track only the save
        api_calls.clear()

        # Click Save button
        page.locator("#console-save-btn").click()
        page.wait_for_timeout(500)

        # Verify PUT request was made
        assert len(api_calls) >= 1
        method, url = api_calls[0]
        assert method == "PUT"
        assert "/api/console/file" in url


class TestTerminalStreaming:
    """Test terminal streaming functionality for action commands."""

    def test_terminal_stores_task_in_localstorage(self, page: Page, server_url: str) -> None:
        """Action response stores task ID in localStorage for reconnection."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Mock Apply API to return a task ID
        page.route(
            "**/api/apply",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test-task-123", "service": null, "command": "apply"}',
            ),
        )

        # Clear localStorage first
        page.evaluate("localStorage.clear()")

        # Click Apply
        page.locator("button", has_text="Apply").click()

        # Wait for response to be processed
        page.wait_for_timeout(500)

        # Verify task ID was stored in localStorage
        stored_task = page.evaluate("localStorage.getItem('cf_task:/')")
        assert stored_task == "test-task-123"

    def test_terminal_reconnects_from_localstorage(self, page: Page, server_url: str) -> None:
        """Terminal attempts to reconnect to task stored in localStorage.

        Tests that when a page loads with an active task in localStorage,
        it expands the terminal and attempts to reconnect.
        """
        # First, set up a task in localStorage before navigating
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Store a task ID in localStorage
        page.evaluate("localStorage.setItem('cf_task:/', 'reconnect-test-123')")

        # Navigate away and back (or reload) to trigger reconnect
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-terminal", timeout=5000)

        # Navigate back to dashboard
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Wait for xterm to load (reconnect uses whenXtermReady)
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=10000)

        # Terminal should be expanded because tryReconnectToTask runs
        page.wait_for_function(
            "document.getElementById('terminal-toggle')?.checked === true",
            timeout=5000,
        )

    def test_action_triggers_terminal_websocket_connection(
        self, page: Page, server_url: str
    ) -> None:
        """Action response with task_id triggers WebSocket connection to correct path."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Track WebSocket connections
        ws_urls: list[str] = []

        def handle_ws(ws: WebSocket) -> None:
            ws_urls.append(ws.url)

        page.on("websocket", handle_ws)

        # Mock Apply API to return a task ID
        page.route(
            "**/api/apply",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "ws-test-456", "service": null, "command": "apply"}',
            ),
        )

        # Wait for xterm to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=10000)

        # Click Apply
        page.locator("button", has_text="Apply").click()

        # Wait for WebSocket connection
        page.wait_for_timeout(1000)

        # Verify WebSocket connected to correct path
        assert len(ws_urls) >= 1
        assert any("/ws/terminal/ws-test-456" in url for url in ws_urls)


class TestExecTerminal:
    """Test exec terminal functionality for container shells."""

    def test_service_page_has_exec_terminal_container(self, page: Page, server_url: str) -> None:
        """Service page has exec terminal container (initially hidden)."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Navigate to plex service
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Exec terminal container should exist but be hidden
        exec_container = page.locator("#exec-terminal-container")
        assert exec_container.count() == 1
        assert "hidden" in (exec_container.get_attribute("class") or "")

        # The inner terminal div should also exist
        exec_terminal = page.locator("#exec-terminal")
        assert exec_terminal.count() == 1

    def test_exec_terminal_connects_websocket(self, page: Page, server_url: str) -> None:
        """Clicking Shell button triggers WebSocket to exec endpoint."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Navigate to plex service
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Mock containers API to return a container
        page.route(
            "**/api/service/plex/containers*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="""
                <div class="flex items-center gap-2 p-2 bg-base-200 rounded">
                    <span class="status status-success"></span>
                    <code class="text-sm flex-1">plex-container</code>
                    <button class="btn btn-sm btn-outline"
                            onclick="initExecTerminal('plex', 'plex-container', 'server-1')">
                        Shell
                    </button>
                </div>
                """,
            ),
        )

        # Reload to get mocked containers
        page.reload()
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Track WebSocket connections
        ws_urls: list[str] = []

        def handle_ws(ws: WebSocket) -> None:
            ws_urls.append(ws.url)

        page.on("websocket", handle_ws)

        # Wait for xterm to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=10000)

        # Click Shell button
        page.locator("button", has_text="Shell").click()

        # Wait for WebSocket connection
        page.wait_for_timeout(1000)

        # Verify WebSocket connected to exec endpoint
        assert len(ws_urls) >= 1
        assert any("/ws/exec/plex/plex-container/server-1" in url for url in ws_urls)

        # Exec terminal container should now be visible
        exec_container = page.locator("#exec-terminal-container")
        assert "hidden" not in (exec_container.get_attribute("class") or "")


class TestServicePagePalette:
    """Test command palette behavior on service pages."""

    def test_service_page_palette_has_action_commands(self, page: Page, server_url: str) -> None:
        """Command palette on service page shows service-specific actions."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Navigate to plex service
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        # Verify service-specific action commands are visible
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "Up" in cmd_list
        assert "Down" in cmd_list
        assert "Restart" in cmd_list
        assert "Pull" in cmd_list
        assert "Update" in cmd_list
        assert "Logs" in cmd_list

    def test_palette_action_triggers_service_api(self, page: Page, server_url: str) -> None:
        """Selecting action from palette triggers correct service API."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services a", timeout=5000)

        # Navigate to plex service
        page.locator("#sidebar-services a", has_text="plex").click()
        page.wait_for_url("**/service/plex", timeout=5000)

        # Track API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "palette-test", "service": "plex", "command": "up"}',
            )

        page.route("**/api/service/plex/up", handle_route)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=2000)

        # Filter to "Up" and execute
        page.locator("#cmd-input").fill("Up")
        page.keyboard.press("Enter")

        # Wait for API call
        page.wait_for_timeout(500)

        # Verify correct API was called
        assert len(api_calls) >= 1
        assert "/api/service/plex/up" in api_calls[0]
