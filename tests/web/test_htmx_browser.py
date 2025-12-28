"""Browser tests for HTMX behavior using Playwright.

Run with: uv run pytest tests/web/test_htmx_browser.py -v --no-cov

CDN assets are cached locally (in .pytest_cache/vendor/) to eliminate network
variability. If a test fails with "Uncached CDN request", add the URL to
compose_farm.web.cdn.CDN_ASSETS.
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
from compose_farm.web.cdn import CDN_ASSETS, ensure_vendor_cache
from compose_farm.web.routes import api as web_api
from compose_farm.web.routes import pages as web_pages

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route, WebSocket

# Default timeout for Playwright waits (ms) - higher for CI stability
TIMEOUT = 10000
SHORT_TIMEOUT = 5000  # For quick UI transitions (palette, dialogs)


def _browser_available() -> bool:
    """Check if any chromium browser is available (system or Playwright-managed)."""
    # Check for system browser
    if shutil.which("chromium") or shutil.which("google-chrome"):
        return True

    # Check for Playwright-managed browser
    try:
        from playwright._impl._driver import compute_driver_executable

        driver_info = compute_driver_executable()
        # compute_driver_executable returns (driver_path, browser_path) tuple
        driver_path = driver_info[0] if isinstance(driver_info, tuple) else driver_info
        return Path(driver_path).exists()
    except Exception:
        return False


# Mark all tests as browser tests and skip if no browser available
pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not _browser_available(),
        reason="No browser available (install via: playwright install chromium --with-deps)",
    ),
]


@pytest.fixture(scope="session")
def vendor_cache(request: pytest.FixtureRequest) -> Path:
    """Download CDN assets once and cache to disk for faster tests."""
    cache_dir = Path(request.config.rootpath) / ".pytest_cache" / "vendor"
    return ensure_vendor_cache(cache_dir)


@pytest.fixture
def page(page: Page, vendor_cache: Path) -> Page:
    """Override default page fixture to intercept CDN requests with local cache.

    Any CDN request not in CDN_ASSETS will abort with an error, forcing developers
    to add new CDN URLs to the cache. This catches both static and dynamic loads.
    """
    cache = {url: (vendor_cache / f, ct) for url, (f, ct) in CDN_ASSETS.items()}

    def handle_cdn(route: Route) -> None:
        url = route.request.url
        for url_prefix, (filepath, content_type) in cache.items():
            if url.startswith(url_prefix):
                route.fulfill(status=200, content_type=content_type, body=filepath.read_bytes())
                return
        # Uncached CDN request - abort with helpful error
        route.abort("failed")
        msg = f"Uncached CDN request: {url}\n\nAdd this URL to CDN_ASSETS in tests/web/test_htmx_browser.py"
        raise RuntimeError(msg)

    page.route(re.compile(r"https://(cdn\.jsdelivr\.net|unpkg\.com)/.*"), handle_cdn)
    return page


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

    Creates a multi-host, multi-stack config for comprehensive testing:
    - server-1: plex (running), grafana (not started)
    - server-2: nextcloud (running), jellyfin (not started)
    """
    tmp: Path = tmp_path_factory.mktemp("data")

    # Create compose dir with stacks
    compose_dir = tmp / "compose"
    compose_dir.mkdir()
    for name in ["plex", "grafana", "nextcloud", "jellyfin", "redis"]:
        svc = compose_dir / name
        svc.mkdir()
        if name == "plex":
            # Multi-service stack for testing service commands
            # Includes hyphenated name (plex-server) to test word-boundary matching
            (svc / "compose.yaml").write_text(
                "services:\n  plex-server:\n    image: test/plex\n  redis:\n    image: redis:alpine\n"
            )
        else:
            (svc / "compose.yaml").write_text(f"services:\n  {name}:\n    image: test/{name}\n")

    # Create glances stack (required for containers page)
    glances_dir = compose_dir / "glances"
    glances_dir.mkdir()
    (glances_dir / "compose.yaml").write_text(
        "services:\n  glances:\n    image: nicolargo/glances\n"
    )

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
stacks:
  plex: server-1
  grafana: server-1
  nextcloud: server-2
  jellyfin: server-2
  redis: server-1
  glances: all
glances_stack: glances
""")

    # Create state (plex and nextcloud running, grafana and jellyfin not started)
    (tmp / "compose-farm-state.yaml").write_text(
        "deployed:\n  plex: server-1\n  nextcloud: server-2\n"
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

    def test_sidebar_loads_stacks_via_htmx(self, page: Page, server_url: str) -> None:
        """Sidebar fetches and displays stacks via hx-get on load."""
        page.goto(server_url)

        # Wait for HTMX to load sidebar content
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Verify actual stacks from test config appear
        stacks = page.locator("#sidebar-stacks li")
        assert stacks.count() == 6  # plex, grafana, nextcloud, jellyfin, redis, glances

        # Check specific stacks are present
        content = page.locator("#sidebar-stacks").inner_text()
        assert "plex" in content
        assert "grafana" in content
        assert "nextcloud" in content
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
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Dashboard content must STILL be visible after sidebar loads
        assert stats.is_visible(), "Dashboard disappeared after sidebar loaded"
        assert page.locator("#stats-cards .card").count() >= 4

    def test_sidebar_shows_running_status(self, page: Page, server_url: str) -> None:
        """Sidebar shows running/stopped status indicators for stacks."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # plex and nextcloud are in state (running) - should have success status
        plex_item = page.locator("#sidebar-stacks li", has_text="plex")
        assert plex_item.locator(".status-success").count() == 1
        nextcloud_item = page.locator("#sidebar-stacks li", has_text="nextcloud")
        assert nextcloud_item.locator(".status-success").count() == 1

        # grafana and jellyfin are NOT in state (not started) - should have neutral status
        grafana_item = page.locator("#sidebar-stacks li", has_text="grafana")
        assert grafana_item.locator(".status-neutral").count() == 1
        jellyfin_item = page.locator("#sidebar-stacks li", has_text="jellyfin")
        assert jellyfin_item.locator(".status-neutral").count() == 1


class TestHTMXBoostNavigation:
    """Test hx-boost SPA-like navigation."""

    def test_navigation_updates_url_without_full_reload(self, page: Page, server_url: str) -> None:
        """Clicking boosted link updates URL without full page reload."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Add a marker to detect full page reload
        page.evaluate("window.__htmxTestMarker = 'still-here'")

        # Click a stack link (boosted via hx-boost on parent)
        page.locator("#sidebar-stacks a", has_text="plex").click()

        # Wait for navigation
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Verify URL changed
        assert "/stack/plex" in page.url

        # Verify NO full page reload (marker should still exist)
        marker = page.evaluate("window.__htmxTestMarker")
        assert marker == "still-here", "Full page reload occurred - hx-boost not working"

    def test_main_content_replaced_on_navigation(self, page: Page, server_url: str) -> None:
        """Navigation replaces #main-content via hx-target/hx-select."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Get initial main content
        initial_content = page.locator("#main-content").inner_text()
        assert "Compose Farm" in initial_content  # Dashboard title

        # Navigate to stack page
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Main content should now show stack page
        new_content = page.locator("#main-content").inner_text()
        assert "plex" in new_content.lower()
        assert "Compose Farm" not in new_content  # Dashboard title should be gone


class TestDashboardContent:
    """Test dashboard displays correct data."""

    def test_stats_show_correct_counts(self, page: Page, server_url: str) -> None:
        """Stats cards show accurate host/stack counts from config."""
        page.goto(server_url)
        page.wait_for_selector("#stats-cards", timeout=TIMEOUT)

        stats = page.locator("#stats-cards").inner_text()

        # From test config: 2 hosts, 5 stacks, 2 running (plex, nextcloud)
        assert "2" in stats  # hosts count
        assert "6" in stats  # stacks count

    def test_pending_shows_not_started_stacks(self, page: Page, server_url: str) -> None:
        """Pending operations shows grafana and jellyfin as not started."""
        page.goto(server_url)
        page.wait_for_selector("#pending-operations", timeout=TIMEOUT)

        pending = page.locator("#pending-operations")
        content = pending.inner_text().lower()

        # grafana and jellyfin are not in state, should show as not started
        assert "grafana" in content or "not started" in content
        assert "jellyfin" in content or "not started" in content

    def test_dashboard_monaco_loads(self, page: Page, server_url: str) -> None:
        """Dashboard page loads Monaco editor for config editing."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Wait for Monaco to load
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=TIMEOUT)

        # Verify Monaco editor element exists (may be in collapsed section)
        page.wait_for_function(
            "document.querySelectorAll('.monaco-editor').length >= 1",
            timeout=TIMEOUT,
        )
        assert page.locator(".monaco-editor").count() >= 1


class TestSaveConfigButton:
    """Test save config button behavior."""

    def test_save_button_shows_saved_feedback(self, page: Page, server_url: str) -> None:
        """Clicking save shows 'Saved!' feedback text."""
        page.goto(server_url)
        page.wait_for_selector("#save-config-btn", timeout=TIMEOUT)

        save_btn = page.locator("#save-config-btn")
        initial_text = save_btn.inner_text()
        assert "Save" in initial_text

        # Click save
        save_btn.click()

        # Wait for feedback
        page.wait_for_function(
            "document.querySelector('#save-config-btn')?.textContent?.includes('Saved')",
            timeout=TIMEOUT,
        )

        # Verify feedback shown
        assert "Saved" in save_btn.inner_text()


class TestStackDetailPage:
    """Test stack detail page via HTMX navigation."""

    def test_stack_page_shows_stack_info(self, page: Page, server_url: str) -> None:
        """Stack page displays stack information."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Should show stack name and host info
        content = page.locator("#main-content").inner_text()
        assert "plex" in content.lower()
        assert "server-1" in content  # assigned host from config
        # Should show compose file path
        assert "compose.yaml" in content

    def test_back_navigation_works(self, page: Page, server_url: str) -> None:
        """Browser back button works after HTMX navigation."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Go back
        page.go_back()
        page.wait_for_url(server_url, timeout=TIMEOUT)

        # Should be back on dashboard
        assert page.url.rstrip("/") == server_url.rstrip("/")

    def test_stack_page_monaco_loads(self, page: Page, server_url: str) -> None:
        """Stack page loads Monaco editor for compose/env editing."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Wait for Monaco to load
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=TIMEOUT)

        # Verify Monaco editor element exists (may be in collapsed section)
        page.wait_for_function(
            "document.querySelectorAll('.monaco-editor').length >= 1",
            timeout=TIMEOUT,
        )
        assert page.locator(".monaco-editor").count() >= 1


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

    def test_text_filter_hides_non_matching_stacks(self, page: Page, server_url: str) -> None:
        """Typing in filter input hides stacks that don't match."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Initially all 6 stacks visible
        visible_items = page.locator("#sidebar-stacks li:not([hidden])")
        assert visible_items.count() == 6

        # Type in filter to match only "plex"
        self._filter_sidebar(page, "plex")

        # Only plex should be visible now
        visible_after = page.locator("#sidebar-stacks li:not([hidden])")
        assert visible_after.count() == 1
        assert "plex" in visible_after.first.inner_text()

    def test_text_filter_updates_count_badge(self, page: Page, server_url: str) -> None:
        """Filter updates the stack count badge."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Initial count should be (6)
        count_badge = page.locator("#sidebar-count")
        assert "(6)" in count_badge.inner_text()

        # Filter to show only stacks containing "x" (plex, nextcloud)
        self._filter_sidebar(page, "x")

        # Count should update to (2)
        assert "(2)" in count_badge.inner_text()

    def test_text_filter_is_case_insensitive(self, page: Page, server_url: str) -> None:
        """Filter matching is case-insensitive."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Type uppercase
        self._filter_sidebar(page, "PLEX")

        # Should still match plex
        visible = page.locator("#sidebar-stacks li:not([hidden])")
        assert visible.count() == 1
        assert "plex" in visible.first.inner_text().lower()

    def test_host_dropdown_filters_by_host(self, page: Page, server_url: str) -> None:
        """Host dropdown filters stacks by their assigned host."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Select server-1 from dropdown
        page.locator("#sidebar-host-select").select_option("server-1")

        # plex, grafana, redis (server-1), and glances (all) should be visible
        visible = page.locator("#sidebar-stacks li:not([hidden])")
        assert visible.count() == 4

        content = visible.all_inner_texts()
        assert any("plex" in s for s in content)
        assert any("grafana" in s for s in content)
        assert any("glances" in s for s in content)
        assert not any("nextcloud" in s for s in content)
        assert not any("jellyfin" in s for s in content)

    def test_combined_text_and_host_filter(self, page: Page, server_url: str) -> None:
        """Text filter and host filter work together."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Filter by server-2 host
        page.locator("#sidebar-host-select").select_option("server-2")

        # Then filter by text "next" (should match only nextcloud on server-2)
        self._filter_sidebar(page, "next")

        visible = page.locator("#sidebar-stacks li:not([hidden])")
        assert visible.count() == 1
        assert "nextcloud" in visible.first.inner_text()

    def test_clearing_filter_shows_all_stacks(self, page: Page, server_url: str) -> None:
        """Clearing filter restores all stacks."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Apply filter
        self._filter_sidebar(page, "plex")
        assert page.locator("#sidebar-stacks li:not([hidden])").count() == 1

        # Clear filter
        self._filter_sidebar(page, "")

        # All stacks visible again
        assert page.locator("#sidebar-stacks li:not([hidden])").count() == 6


class TestCommandPalette:
    """Test command palette (Cmd+K) JavaScript functionality."""

    def test_cmd_k_opens_palette(self, page: Page, server_url: str) -> None:
        """Cmd+K keyboard shortcut opens the command palette."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Palette should be closed initially
        assert not page.locator("#cmd-palette").is_visible()

        # Press Cmd+K (Meta+k on Mac, Control+k otherwise)
        page.keyboard.press("Control+k")

        # Palette should now be open
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)
        assert page.locator("#cmd-palette").is_visible()

    def test_palette_input_is_focused_on_open(self, page: Page, server_url: str) -> None:
        """Input field is focused when palette opens."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Input should be focused - we can type directly
        page.keyboard.type("test")
        assert page.locator("#cmd-input").input_value() == "test"

    def test_palette_shows_navigation_commands(self, page: Page, server_url: str) -> None:
        """Palette shows Dashboard and Console navigation commands."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        cmd_list = page.locator("#cmd-list").inner_text()
        assert "Dashboard" in cmd_list
        assert "Console" in cmd_list

    def test_palette_shows_stack_navigation(self, page: Page, server_url: str) -> None:
        """Palette includes stack names for navigation."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        cmd_list = page.locator("#cmd-list").inner_text()
        # Stacks should appear as navigation options
        assert "plex" in cmd_list
        assert "nextcloud" in cmd_list

    def test_palette_filters_on_input(self, page: Page, server_url: str) -> None:
        """Typing in palette filters the command list."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type to filter
        page.locator("#cmd-input").fill("plex")

        # Should show plex, hide others
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "plex" in cmd_list
        assert "Dashboard" not in cmd_list  # Filtered out

    def test_arrow_down_moves_selection(self, page: Page, server_url: str) -> None:
        """Arrow down key moves selection to next item."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

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
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to plex stack
        page.locator("#cmd-input").fill("plex")
        page.keyboard.press("Enter")

        # Palette should close (use state="hidden" since closed dialog is not visible)
        page.wait_for_selector("#cmd-palette", state="hidden", timeout=SHORT_TIMEOUT)

        # Should navigate to plex stack page
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

    def test_click_executes_command(self, page: Page, server_url: str) -> None:
        """Clicking a command executes it."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Click on Console command
        page.locator("#cmd-list a", has_text="Console").click()

        # Should navigate to console page
        page.wait_for_url("**/console", timeout=TIMEOUT)

    def test_escape_closes_palette(self, page: Page, server_url: str) -> None:
        """Escape key closes the palette without executing."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        page.keyboard.press("Escape")

        # Palette should close, URL unchanged (use state="hidden" since closed dialog is not visible)
        page.wait_for_selector("#cmd-palette", state="hidden", timeout=SHORT_TIMEOUT)
        assert page.url.rstrip("/") == server_url.rstrip("/")

    def test_fab_button_opens_palette(self, page: Page, server_url: str) -> None:
        """Floating action button opens the command palette."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Click the FAB
        page.locator("#cmd-fab").click()

        # Palette should open
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)


class TestActionButtons:
    """Test action button HTMX POST requests."""

    def test_apply_button_makes_post_request(self, page: Page, server_url: str) -> None:
        """Apply button triggers POST to /api/apply."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

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
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

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
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

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
            timeout=SHORT_TIMEOUT,
        )

    def test_stack_page_action_buttons(self, page: Page, server_url: str) -> None:
        """Service page has working action buttons."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Intercept stack-specific API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test-up-123"}',
            )

        page.route("**/api/stack/plex/up", handle_route)

        # Click Up button (use get_by_role for exact match, avoiding "Update")
        page.get_by_role("button", name="Up", exact=True).click()
        page.wait_for_timeout(500)

        assert len(api_calls) == 1
        assert "/api/stack/plex/up" in api_calls[0]


class TestKeyboardShortcuts:
    """Test global keyboard shortcuts."""

    def test_ctrl_s_triggers_save(self, page: Page, server_url: str) -> None:
        """Ctrl+S triggers save when editors are present."""
        page.goto(server_url)
        page.wait_for_selector("#save-config-btn", timeout=TIMEOUT)

        # Wait for Monaco editor to load (it takes a moment)
        page.wait_for_function(
            "typeof monaco !== 'undefined'",
            timeout=TIMEOUT,
        )

        # Press Ctrl+S
        page.keyboard.press("Control+s")

        # Should trigger save - button shows "Saved!"
        page.wait_for_function(
            "document.querySelector('#save-config-btn')?.textContent?.includes('Saved')",
            timeout=TIMEOUT,
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
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)
        page.wait_for_load_state("networkidle")

        # All major dashboard sections must be visible
        assert page.locator("#stats-cards").is_visible(), "Stats cards missing"
        assert page.locator("#stats-cards .card").count() >= 4, "Stats incomplete"
        assert page.locator("#pending-operations").is_visible(), "Pending ops missing"
        assert page.locator("#stacks-by-host").is_visible(), "Stacks by host missing"
        assert page.locator("#sidebar-stacks").is_visible(), "Sidebar missing"

    def test_sidebar_persists_after_navigation_and_back(self, page: Page, server_url: str) -> None:
        """Sidebar content persists through navigation cycle."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Remember sidebar state
        initial_count = page.locator("#sidebar-stacks li").count()
        assert initial_count == 6

        # Navigate away
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Sidebar should still be there with same content
        assert page.locator("#sidebar-stacks").is_visible()
        assert page.locator("#sidebar-stacks li").count() == initial_count

        # Navigate back
        page.go_back()
        page.wait_for_url(server_url, timeout=TIMEOUT)

        # Sidebar still intact
        assert page.locator("#sidebar-stacks").is_visible()
        assert page.locator("#sidebar-stacks li").count() == initial_count

    def test_dashboard_sections_persist_after_save(self, page: Page, server_url: str) -> None:
        """Dashboard sections remain after save triggers cf:refresh event."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Capture initial state - all must be visible
        assert page.locator("#stats-cards").is_visible()
        assert page.locator("#pending-operations").is_visible()
        assert page.locator("#stacks-by-host").is_visible()

        # Trigger save (which dispatches cf:refresh)
        page.locator("#save-config-btn").click()
        page.wait_for_function(
            "document.querySelector('#save-config-btn')?.textContent?.includes('Saved')",
            timeout=TIMEOUT,
        )

        # Wait for refresh requests to complete
        page.wait_for_load_state("networkidle")

        # All sections must still be visible
        assert page.locator("#stats-cards").is_visible(), "Stats disappeared after save"
        assert page.locator("#pending-operations").is_visible(), "Pending disappeared"
        assert page.locator("#stacks-by-host").is_visible(), "Stacks disappeared"
        assert page.locator("#sidebar-stacks").is_visible(), "Sidebar disappeared"

    def test_filter_state_not_affected_by_other_htmx_requests(
        self, page: Page, server_url: str
    ) -> None:
        """Sidebar filter state persists during other HTMX activity."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Apply a filter
        filter_input = page.locator("#sidebar-filter")
        filter_input.fill("plex")
        filter_input.dispatch_event("keyup")

        # Verify filter is applied
        assert page.locator("#sidebar-stacks li:not([hidden])").count() == 1

        # Trigger a save (causes cf:refresh on multiple elements)
        page.locator("#save-config-btn").click()
        page.wait_for_timeout(1000)

        # Filter input should still have our text
        # (Note: sidebar reloads so filter clears - this tests the sidebar reload works)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)
        assert page.locator("#sidebar-stacks").is_visible()

    def test_main_content_not_affected_by_sidebar_refresh(
        self, page: Page, server_url: str
    ) -> None:
        """Main content area stays intact when sidebar refreshes."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

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
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Count initial elements
        initial_stat_count = page.locator("#stats-cards .card").count()
        initial_stack_count = page.locator("#sidebar-stacks li").count()

        # Trigger multiple refreshes
        for _ in range(3):
            page.evaluate("document.body.dispatchEvent(new CustomEvent('cf:refresh'))")
            page.wait_for_timeout(300)

        page.wait_for_load_state("networkidle")

        # Counts should be same (no duplicates created)
        assert page.locator("#stats-cards .card").count() == initial_stat_count
        assert page.locator("#sidebar-stacks li").count() == initial_stack_count


class TestConsolePage:
    """Test console page functionality."""

    def test_console_page_renders(self, page: Page, server_url: str) -> None:
        """Console page renders with all required elements."""
        page.goto(f"{server_url}/console")

        # Wait for page to load
        page.wait_for_selector("#console-host-select", timeout=TIMEOUT)

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
        page.wait_for_selector("#console-host-select", timeout=TIMEOUT)

        # Get all options from the dropdown
        options = page.locator("#console-host-select option")
        assert options.count() == 2  # server-1 and server-2 from test config

        # Verify both hosts are present
        option_texts = [options.nth(i).inner_text() for i in range(options.count())]
        assert any("server-1" in text for text in option_texts)
        assert any("server-2" in text for text in option_texts)

    def test_console_connect_shows_status(self, page: Page, server_url: str) -> None:
        """Console shows connection status when attempting to connect."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-host-select", timeout=TIMEOUT)

        # Wait for terminal to initialize (triggers auto-connect)
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-terminal .xterm", timeout=TIMEOUT)

        # Status element should exist and have some text
        # (may show "Connected", "Connecting...", or "Disconnected" depending on WebSocket)
        status = page.locator("#console-status")
        assert status.is_visible()
        status_text = status.inner_text()
        # Should show some connection-related status
        assert any(s in status_text for s in ["Connect", "Disconnect", "server-"])

    def test_console_connect_creates_terminal_element(self, page: Page, server_url: str) -> None:
        """Connecting to a host creates xterm terminal elements.

        The console page auto-connects to the first host on load,
        which creates the xterm.js terminal inside the container.
        """
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-terminal", timeout=TIMEOUT)

        # Wait for xterm.js to load from CDN
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)

        # The console page auto-connects, which creates the terminal.
        # Wait for xterm to initialize (creates .xterm class)
        page.wait_for_selector("#console-terminal .xterm", timeout=TIMEOUT)

        # Verify xterm elements are present
        xterm_container = page.locator("#console-terminal .xterm")
        assert xterm_container.is_visible()

        # Verify xterm screen is created (the actual terminal display)
        xterm_screen = page.locator("#console-terminal .xterm-screen")
        assert xterm_screen.is_visible()

    def test_console_editor_initializes(self, page: Page, server_url: str) -> None:
        """Monaco editor initializes on the console page."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-editor", timeout=TIMEOUT)

        # Wait for Monaco to load from CDN
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=TIMEOUT)

        # Monaco creates elements inside the container
        page.wait_for_selector("#console-editor .monaco-editor", timeout=TIMEOUT)

        # Verify Monaco editor is present
        monaco_editor = page.locator("#console-editor .monaco-editor")
        assert monaco_editor.is_visible()

    def test_console_load_file_calls_api(self, page: Page, server_url: str) -> None:
        """Clicking Open button calls the file API with correct parameters."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-file-path", timeout=TIMEOUT)

        # Wait for terminal to connect (sets currentHost)
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-terminal .xterm", timeout=TIMEOUT)

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
        page.wait_for_selector("#console-file-path", timeout=TIMEOUT)

        # Wait for terminal to connect and Monaco to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-terminal .xterm", timeout=TIMEOUT)
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-editor .monaco-editor", timeout=TIMEOUT)

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
            timeout=TIMEOUT,
        )

    def test_console_load_file_updates_status(self, page: Page, server_url: str) -> None:
        """Loading a file updates the editor status to show the file path."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-file-path", timeout=TIMEOUT)

        # Wait for terminal and Monaco
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-terminal .xterm", timeout=TIMEOUT)
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-editor .monaco-editor", timeout=TIMEOUT)

        # Mock file API
        page.route(
            "**/api/console/file*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"success": true, "content": "test"}',
            ),
        )

        # Load file
        file_input = page.locator("#console-file-path")
        file_input.fill("/tmp/test.yaml")
        page.locator("button", has_text="Open").click()

        # Wait for status to show "Loaded:"
        page.wait_for_function(
            "document.getElementById('editor-status')?.textContent?.includes('Loaded')",
            timeout=TIMEOUT,
        )

        # Verify status shows the file path
        status = page.locator("#editor-status").inner_text()
        assert "Loaded" in status
        assert "test.yaml" in status

    def test_console_save_file_calls_api(self, page: Page, server_url: str) -> None:
        """Clicking Save button calls the file API with PUT method."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-file-path", timeout=TIMEOUT)

        # Wait for terminal to connect and Monaco to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-terminal .xterm", timeout=TIMEOUT)
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-editor .monaco-editor", timeout=TIMEOUT)

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

    def test_console_save_file_updates_status(self, page: Page, server_url: str) -> None:
        """Saving a file updates the editor status to show 'Saved'."""
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-file-path", timeout=TIMEOUT)

        # Wait for terminal and Monaco
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-terminal .xterm", timeout=TIMEOUT)
        page.wait_for_function("typeof monaco !== 'undefined'", timeout=TIMEOUT)
        page.wait_for_selector("#console-editor .monaco-editor", timeout=TIMEOUT)

        # Mock file API for both load and save
        def handle_route(route: Route) -> None:
            if route.request.method == "PUT":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success": true}',
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success": true, "content": "test"}',
                )

        page.route("**/api/console/file*", handle_route)

        # Load file first
        file_input = page.locator("#console-file-path")
        file_input.fill("/tmp/test.yaml")
        page.locator("button", has_text="Open").click()
        page.wait_for_function(
            "document.getElementById('editor-status')?.textContent?.includes('Loaded')",
            timeout=TIMEOUT,
        )

        # Save file
        page.locator("#console-save-btn").click()

        # Wait for status to show "Saved:"
        page.wait_for_function(
            "document.getElementById('editor-status')?.textContent?.includes('Saved')",
            timeout=TIMEOUT,
        )

        # Verify status shows saved
        status = page.locator("#editor-status").inner_text()
        assert "Saved" in status


class TestTerminalStreaming:
    """Test terminal streaming functionality for action commands."""

    def test_terminal_stores_task_in_localstorage(self, page: Page, server_url: str) -> None:
        """Action response stores task ID in localStorage for reconnection."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Mock Apply API to return a task ID
        page.route(
            "**/api/apply",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test-task-123", "stack": null, "command": "apply"}',
            ),
        )

        # Clear localStorage first
        page.evaluate("localStorage.clear()")

        # Click Apply
        page.locator("button", has_text="Apply").click()

        # Poll for localStorage to be set (more reliable than fixed wait)
        page.wait_for_function(
            "localStorage.getItem('cf_task:/') === 'test-task-123'",
            timeout=TIMEOUT,
        )

    def test_terminal_reconnects_from_localstorage(self, page: Page, server_url: str) -> None:
        """Terminal attempts to reconnect to task stored in localStorage.

        Tests that when a page loads with an active task in localStorage,
        it expands the terminal and attempts to reconnect.
        """
        # First, set up a task in localStorage before navigating
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Store a task ID in localStorage
        page.evaluate("localStorage.setItem('cf_task:/', 'reconnect-test-123')")

        # Navigate away and back (or reload) to trigger reconnect
        page.goto(f"{server_url}/console")
        page.wait_for_selector("#console-terminal", timeout=TIMEOUT)

        # Navigate back to dashboard
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Wait for xterm to load (reconnect uses whenXtermReady)
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)

        # Terminal should be expanded because tryReconnectToTask runs
        page.wait_for_function(
            "document.getElementById('terminal-toggle')?.checked === true",
            timeout=TIMEOUT,
        )

    def test_action_triggers_terminal_websocket_connection(
        self, page: Page, server_url: str
    ) -> None:
        """Action response with task_id triggers WebSocket connection to correct path."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

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
                body='{"task_id": "ws-test-456", "stack": null, "command": "apply"}',
            ),
        )

        # Wait for xterm to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)

        # Click Apply
        page.locator("button", has_text="Apply").click()

        # Wait for WebSocket connection
        page.wait_for_timeout(1000)

        # Verify WebSocket connected to correct path
        assert len(ws_urls) >= 1
        assert any("/ws/terminal/ws-test-456" in url for url in ws_urls)

    def test_terminal_displays_connected_message(self, page: Page, server_url: str) -> None:
        """Terminal shows [Connected] message after WebSocket opens."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Mock Apply API to return a task ID
        page.route(
            "**/api/apply",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "connected-test", "stack": null, "command": "apply"}',
            ),
        )

        # Wait for xterm to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)

        # Click Apply to trigger terminal
        page.locator("button", has_text="Apply").click()

        # Wait for terminal to be created and WebSocket to connect
        page.wait_for_selector("#terminal-output .xterm", timeout=TIMEOUT)

        # Wait for [Connected] message to appear in terminal
        # xterm.js renders content into .xterm-rows
        page.wait_for_function(
            """() => {
                const viewport = document.querySelector('#terminal-output .xterm-rows');
                return viewport && viewport.textContent.includes('Connected');
            }""",
            timeout=TIMEOUT,
        )


class TestExecTerminal:
    """Test exec terminal functionality for container shells."""

    def test_stack_page_has_exec_terminal_container(self, page: Page, server_url: str) -> None:
        """Service page has exec terminal container (initially hidden)."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

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
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Mock containers API to return a container
        page.route(
            "**/api/stack/plex/containers*",
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
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Track WebSocket connections
        ws_urls: list[str] = []

        def handle_ws(ws: WebSocket) -> None:
            ws_urls.append(ws.url)

        page.on("websocket", handle_ws)

        # Wait for xterm to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)

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
    """Test command palette behavior on stack pages."""

    def test_stack_page_palette_has_action_commands(self, page: Page, server_url: str) -> None:
        """Command palette on stack page shows stack-specific actions."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Verify stack-specific action commands are visible
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "Up" in cmd_list
        assert "Down" in cmd_list
        assert "Restart" in cmd_list
        assert "Pull" in cmd_list
        assert "Update" in cmd_list
        assert "Logs" in cmd_list

    def test_palette_action_triggers_stack_api(self, page: Page, server_url: str) -> None:
        """Selecting action from palette triggers correct stack API."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Track API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "palette-test", "stack": "plex", "command": "up"}',
            )

        page.route("**/api/stack/plex/up", handle_route)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to "Up" and execute
        page.locator("#cmd-input").fill("Up")
        page.keyboard.press("Enter")

        # Wait for API call
        page.wait_for_timeout(500)

        # Verify correct API was called
        assert len(api_calls) >= 1
        assert "/api/stack/plex/up" in api_calls[0]

    def test_palette_apply_from_stack_page(self, page: Page, server_url: str) -> None:
        """Selecting Apply from stack page palette navigates to dashboard and triggers API."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Track API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "apply-test", "stack": null, "command": "apply"}',
            )

        page.route("**/api/apply", handle_route)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to "Apply" and execute
        page.locator("#cmd-input").fill("Apply")
        page.keyboard.press("Enter")

        # Wait for navigation to dashboard and API call
        page.wait_for_url(server_url, timeout=TIMEOUT)
        page.wait_for_timeout(500)

        # Verify Apply API was called
        assert len(api_calls) >= 1
        assert "/api/apply" in api_calls[0]

    def test_palette_shows_service_commands(self, page: Page, server_url: str) -> None:
        """Command palette on stack page shows service-specific commands."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack (has plex and redis services)
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to service commands
        page.locator("#cmd-input").fill("Restart:")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should show restart commands for both services
        assert "Restart: plex-server" in cmd_list
        assert "Restart: redis" in cmd_list

    def test_palette_service_commands_for_all_actions(self, page: Page, server_url: str) -> None:
        """Service commands include all expected actions (restart, pull, logs, stop, up)."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Check all service action types exist for the plex-server service
        actions = ["Restart", "Pull", "Logs", "Stop", "Up"]
        for action in actions:
            page.locator("#cmd-input").fill(f"{action}: plex-server")
            cmd_list = page.locator("#cmd-list").inner_text()
            assert f"{action}: plex-server" in cmd_list, f"Missing {action}: plex-server command"

    def test_palette_service_command_triggers_api(self, page: Page, server_url: str) -> None:
        """Selecting service command triggers correct service API endpoint."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Track API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "svc-test", "stack": "plex", "service": "redis", "command": "restart"}',
            )

        page.route("**/api/stack/plex/service/redis/restart", handle_route)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to Restart:redis and execute
        page.locator("#cmd-input").fill("Restart: redis")
        page.keyboard.press("Enter")

        # Wait for API call
        page.wait_for_timeout(500)

        # Verify correct service API was called
        assert len(api_calls) >= 1
        assert "/api/stack/plex/service/redis/restart" in api_calls[0]

    def test_palette_service_commands_have_teal_indicator(
        self, page: Page, server_url: str
    ) -> None:
        """Service commands display with teal color indicator."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to a service command
        page.locator("#cmd-input").fill("Restart: plex-server")

        # Get the command element and check its border color
        cmd_item = page.locator("#cmd-list a", has_text="Restart: plex-server").first
        style = cmd_item.get_attribute("style") or ""

        # Service commands should have teal color (#14b8a6)
        assert "#14b8a6" in style, f"Expected teal border color, got style: {style}"

    def test_single_service_stack_shows_service_commands(self, page: Page, server_url: str) -> None:
        """Single-service stacks also show service commands."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks li", timeout=TIMEOUT)

        # Navigate to redis stack (has only redis service)
        redis_link = page.locator("#sidebar-stacks a", has_text="redis")
        redis_link.wait_for(timeout=TIMEOUT)
        redis_link.click()
        page.wait_for_url("**/stack/redis", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to service commands
        page.locator("#cmd-input").fill("Restart:")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should show restart command for redis service
        assert "Restart: redis" in cmd_list

    def test_palette_filter_without_colon(self, page: Page, server_url: str) -> None:
        """Filter matches service commands without colon (e.g., 'Up redis' matches 'Up: redis')."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type "Restart redis" without colon
        page.locator("#cmd-input").fill("Restart redis")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should still match "Restart: redis"
        assert "Restart: redis" in cmd_list

    def test_palette_fuzzy_filter_partial_words(self, page: Page, server_url: str) -> None:
        """Filter matches with partial words (e.g., 'rest red' matches 'Restart: redis')."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type partial words "rest red"
        page.locator("#cmd-input").fill("rest red")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should match "Restart: redis"
        assert "Restart: redis" in cmd_list

    def test_palette_fuzzy_filter_any_order(self, page: Page, server_url: str) -> None:
        """Filter matches words in any order (e.g., 'redis rest' matches 'Restart: redis')."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type words in reverse order "redis rest"
        page.locator("#cmd-input").fill("redis rest")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should match "Restart: redis"
        assert "Restart: redis" in cmd_list

    def test_palette_filter_without_colon_triggers_api(self, page: Page, server_url: str) -> None:
        """Service command filtered without colon still triggers correct API."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Track API calls
        api_calls: list[str] = []

        def handle_route(route: Route) -> None:
            api_calls.append(route.request.url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "test", "stack": "plex", "service": "redis", "command": "pull"}',
            )

        page.route("**/api/stack/plex/service/redis/pull", handle_route)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type "Pull redis" without colon and execute
        page.locator("#cmd-input").fill("Pull redis")
        page.keyboard.press("Enter")

        # Wait for API call
        page.wait_for_timeout(500)

        # Verify correct service API was called
        assert len(api_calls) >= 1
        assert "/api/stack/plex/service/redis/pull" in api_calls[0]

    def test_palette_hyphenated_service_name(self, page: Page, server_url: str) -> None:
        """Filter matches hyphenated service names by second word (e.g., 'server' matches 'plex-server')."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack (has plex-server service)
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type just "server" - should match "plex-server" because hyphen splits words
        page.locator("#cmd-input").fill("Restart server")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should match "Restart: plex-server"
        assert "Restart: plex-server" in cmd_list

        # Also verify "rest plex" matches via the first part of hyphenated name
        page.locator("#cmd-input").fill("rest plex")
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "Restart: plex-server" in cmd_list

    def test_shell_commands_in_palette(self, page: Page, server_url: str) -> None:
        """Command palette includes Shell commands for each service."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to shell commands
        page.locator("#cmd-input").fill("Shell:")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should have Shell commands for plex-server and redis services
        assert "Shell: plex-server" in cmd_list
        assert "Shell: redis" in cmd_list

    def test_shell_command_fuzzy_match(self, page: Page, server_url: str) -> None:
        """Shell commands can be found with fuzzy search."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Open command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type "shell redis" without colon
        page.locator("#cmd-input").fill("shell redis")
        cmd_list = page.locator("#cmd-list").inner_text()

        # Should match "Shell: redis"
        assert "Shell: redis" in cmd_list


class TestThemeSwitcher:
    """Test theme switcher via command palette."""

    @staticmethod
    def _open_theme_palette(page: Page) -> None:
        """Open the command palette with theme filter by clicking the theme button."""
        page.locator("#theme-btn").click()
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

    @staticmethod
    def _select_theme(page: Page, theme: str) -> None:
        """Select a theme from the command palette."""
        # Filter to the specific theme
        page.locator("#cmd-input").fill(f"theme: {theme}")
        page.keyboard.press("Enter")

    def test_theme_button_exists(self, page: Page, server_url: str) -> None:
        """Theme button exists in sidebar header."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Theme button should exist
        assert page.locator("#theme-btn").count() == 1

    def test_theme_button_opens_palette_with_filter(self, page: Page, server_url: str) -> None:
        """Clicking theme button opens command palette pre-filtered to themes."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        self._open_theme_palette(page)

        # Input should have "theme:" filter
        assert page.locator("#cmd-input").input_value() == "theme:"

        # Should show theme options
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "light" in cmd_list
        assert "dark" in cmd_list

    def test_clicking_theme_changes_html_data_theme(self, page: Page, server_url: str) -> None:
        """Selecting a theme changes the data-theme attribute on <html>."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Get initial theme
        initial_theme = page.locator("html").get_attribute("data-theme")

        # Select a different theme
        target_theme = "cupcake" if initial_theme != "cupcake" else "dracula"
        self._open_theme_palette(page)
        self._select_theme(page, target_theme)

        # Verify the html element's data-theme changed
        new_theme = page.locator("html").get_attribute("data-theme")
        assert new_theme == target_theme, f"Expected {target_theme}, got {new_theme}"

    def test_theme_persists_in_localstorage(self, page: Page, server_url: str) -> None:
        """Selected theme is saved to localStorage."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Select synthwave theme
        self._open_theme_palette(page)
        self._select_theme(page, "synthwave")

        # Check localStorage
        stored = page.evaluate("localStorage.getItem('cf_theme')")
        assert stored == "synthwave"

    def test_theme_restored_on_page_load(self, page: Page, server_url: str) -> None:
        """Theme is restored from localStorage on page load."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Set theme
        self._open_theme_palette(page)
        self._select_theme(page, "retro")

        # Reload page
        page.reload()
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Theme should be restored
        theme = page.locator("html").get_attribute("data-theme")
        assert theme == "retro"

    def test_theme_can_be_changed_multiple_times(self, page: Page, server_url: str) -> None:
        """Theme can be changed multiple times in a session."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        themes_to_test = ["light", "dark", "nord", "sunset"]

        for theme in themes_to_test:
            self._open_theme_palette(page)
            self._select_theme(page, theme)
            current = page.locator("html").get_attribute("data-theme")
            assert current == theme, f"Failed to switch to {theme}, got {current}"

    def test_themes_available_in_regular_palette(self, page: Page, server_url: str) -> None:
        """Themes are also available when opening regular command palette."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Open with Cmd+K
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type theme filter
        page.locator("#cmd-input").fill("theme:")

        # Should show theme options
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "theme: light" in cmd_list
        assert "theme: dark" in cmd_list

    def test_theme_filter_without_colon(self, page: Page, server_url: str) -> None:
        """Filter matches theme commands without colon (e.g., 'theme dark' matches 'theme: dark')."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Open with Cmd+K
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Type "theme dark" without colon
        page.locator("#cmd-input").fill("theme dark")

        # Should show theme: dark option
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "theme: dark" in cmd_list

    def test_theme_command_opens_theme_picker(self, page: Page, server_url: str) -> None:
        """Selecting 'Theme' command reopens palette with theme filter."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Open palette and select Theme command
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)

        # Filter to Theme command and select it
        page.locator("#cmd-input").fill("Theme")
        page.keyboard.press("Enter")

        # Palette should reopen with "theme:" filter
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)
        assert page.locator("#cmd-input").input_value() == "theme:"

        # Should show theme options
        cmd_list = page.locator("#cmd-list").inner_text()
        assert "theme: light" in cmd_list

    def test_current_theme_is_preselected(self, page: Page, server_url: str) -> None:
        """Opening theme picker pre-selects the current theme."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Set a specific theme first
        self._open_theme_palette(page)
        self._select_theme(page, "dracula")

        # Reopen theme palette
        self._open_theme_palette(page)

        # The selected item (with bg-base-300) should be dracula
        selected_item = page.locator("#cmd-list a.bg-base-300")
        assert selected_item.count() == 1
        assert "dracula" in selected_item.inner_text()

    def test_theme_shows_color_swatches(self, page: Page, server_url: str) -> None:
        """Theme commands show color preview swatches."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        self._open_theme_palette(page)

        # Theme items should have color swatches with data-theme attribute
        light_swatch = page.locator("#cmd-list [data-theme='light']")
        assert light_swatch.count() >= 1

        # Swatch should contain bg-primary, bg-secondary, etc.
        swatch_html = light_swatch.first.inner_html()
        assert "bg-primary" in swatch_html
        assert "bg-secondary" in swatch_html

    def test_theme_preview_on_arrow_navigation(self, page: Page, server_url: str) -> None:
        """Arrow key navigation previews themes without persisting."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Set initial theme
        self._open_theme_palette(page)
        self._select_theme(page, "dark")

        # Reopen palette
        self._open_theme_palette(page)

        # Navigate to a different theme with arrow keys
        page.locator("#cmd-input").fill("theme: cupcake")
        page.keyboard.press("ArrowDown")  # Select cupcake

        # Theme should be previewed
        current = page.locator("html").get_attribute("data-theme")
        assert current == "cupcake"

        # But localStorage should still have original
        stored = page.evaluate("localStorage.getItem('cf_theme')")
        assert stored == "dark"

        # Press Escape to cancel
        page.keyboard.press("Escape")

        # Theme should be restored
        current = page.locator("html").get_attribute("data-theme")
        assert current == "dark"

    def test_theme_preview_restored_on_escape(self, page: Page, server_url: str) -> None:
        """Pressing Escape restores original theme."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Set initial theme
        self._open_theme_palette(page)
        self._select_theme(page, "nord")

        initial = page.locator("html").get_attribute("data-theme")
        assert initial == "nord"

        # Open palette and navigate to different theme
        self._open_theme_palette(page)
        page.locator("#cmd-input").fill("theme: synthwave")

        # Preview should change
        page.wait_for_function(
            "document.documentElement.getAttribute('data-theme') === 'synthwave'"
        )

        # Press Escape
        page.keyboard.press("Escape")

        # Should restore original
        restored = page.locator("html").get_attribute("data-theme")
        assert restored == "nord"

    def test_theme_restored_after_non_theme_command(self, page: Page, server_url: str) -> None:
        """Theme restores when executing non-theme command after preview."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Set initial theme to dark
        self._open_theme_palette(page)
        self._select_theme(page, "dark")

        # Open palette, navigate to a theme to preview it
        self._open_theme_palette(page)
        page.locator("#cmd-input").fill("theme: cupcake")
        page.wait_for_timeout(200)

        # cupcake should be previewed
        assert page.locator("html").get_attribute("data-theme") == "cupcake"

        # Now filter to Dashboard (non-theme command)
        page.locator("#cmd-input").fill("Dashboard")
        page.wait_for_timeout(200)

        # Theme should restore since Dashboard has no themeId
        current = page.locator("html").get_attribute("data-theme")
        assert current == "dark", f"Expected dark after filtering to Dashboard, got {current}"

        # Execute Dashboard
        page.keyboard.press("Enter")
        page.wait_for_selector("#cmd-palette:not([open])", timeout=SHORT_TIMEOUT)

        # Theme should still be dark
        final = page.locator("html").get_attribute("data-theme")
        assert final == "dark", f"Expected dark after executing Dashboard, got {final}"


class TestTerminalNavigationIsolation:
    """Test that terminal connections are properly isolated per page.

    Regression tests for a bug where navigating away from a stack page via
    command palette would cause the new page to reconnect to the old page's
    terminal task because history.pushState hadn't updated the URL yet when
    tryReconnectToTask() ran.
    """

    def test_stack_terminal_not_reconnected_on_dashboard(self, page: Page, server_url: str) -> None:
        """Terminal started on stack page should NOT reconnect when navigating to dashboard.

        Bug scenario:
        1. On /stack/plex, click Update → terminal connects, task stored at cf_task:/stack/plex
        2. Navigate to dashboard via command palette
        3. Dashboard loads, htmx:afterSwap fires
        4. tryReconnectToTask() runs but window.location.pathname is still /stack/plex
           (because pushState hasn't run yet)
        5. Bug: Dashboard reconnects to plex's terminal task

        Expected: Dashboard should NOT have any task_id in its localStorage key (cf_task:/)
        """
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks a", timeout=TIMEOUT)

        # Navigate to plex stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Clear any existing task state
        page.evaluate("localStorage.clear()")

        # Track WebSocket connections to see which terminals are opened
        ws_urls: list[str] = []

        def handle_ws(ws: WebSocket) -> None:
            ws_urls.append(ws.url)

        page.on("websocket", handle_ws)

        # Mock Update API to return a task ID
        page.route(
            "**/api/stack/plex/update",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"task_id": "plex-update-task-123", "stack": "plex", "command": "update"}',
            ),
        )

        # Wait for xterm to load
        page.wait_for_function("typeof Terminal !== 'undefined'", timeout=TIMEOUT)

        # Click Update button on plex stack page
        page.locator("button", has_text="Update").click()

        # Wait for terminal to connect
        page.wait_for_selector("#terminal-output .xterm", timeout=TIMEOUT)
        page.wait_for_timeout(500)

        # Verify task was stored for /stack/plex
        plex_task = page.evaluate("localStorage.getItem('cf_task:/stack/plex')")
        assert plex_task == "plex-update-task-123", (
            f"Expected task stored at /stack/plex, got {plex_task}"
        )

        # Dashboard should have NO task yet
        dashboard_task_before = page.evaluate("localStorage.getItem('cf_task:/')")
        assert dashboard_task_before is None, (
            f"Dashboard should have no task before navigation, got {dashboard_task_before}"
        )

        # Count current WebSocket connections
        ws_count_before = len(ws_urls)

        # Navigate to dashboard via command palette (this triggers the bug)
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)
        page.locator("#cmd-input").fill("Dashboard")
        page.keyboard.press("Enter")

        # Wait for navigation to complete
        page.wait_for_url(server_url, timeout=TIMEOUT)
        page.wait_for_selector("#stats-cards", timeout=TIMEOUT)

        # Give time for any erroneous reconnection attempts
        page.wait_for_timeout(1000)

        # CRITICAL ASSERTION: Dashboard should NOT have a task in localStorage
        dashboard_task_after = page.evaluate("localStorage.getItem('cf_task:/')")
        assert dashboard_task_after is None, (
            f"Bug detected: Dashboard incorrectly has task '{dashboard_task_after}' in localStorage. "
            "This means tryReconnectToTask() ran before pushState updated the URL."
        )

        # CRITICAL ASSERTION: No new WebSocket should have been opened for the plex task
        # after navigating to dashboard
        new_ws_urls = ws_urls[ws_count_before:]
        plex_reconnect_attempts = [url for url in new_ws_urls if "plex-update-task-123" in url]
        assert len(plex_reconnect_attempts) == 0, (
            f"Bug detected: Dashboard attempted to reconnect to plex task. "
            f"New WebSocket URLs after navigation: {new_ws_urls}"
        )

    def test_dashboard_terminal_not_shown_after_stack_navigation(
        self, page: Page, server_url: str
    ) -> None:
        """Dashboard's terminal should remain collapsed when navigating away and back.

        This tests that navigating from dashboard → stack → dashboard doesn't
        cause the terminal to expand unexpectedly.
        """
        page.goto(server_url)
        page.wait_for_selector("#sidebar-stacks", timeout=TIMEOUT)

        # Terminal should be collapsed on dashboard
        terminal_toggle = page.locator("#terminal-toggle")
        assert not terminal_toggle.is_checked(), "Terminal should be collapsed initially"

        # Navigate to a stack
        page.locator("#sidebar-stacks a", has_text="plex").click()
        page.wait_for_url("**/stack/plex", timeout=TIMEOUT)

        # Navigate back to dashboard via command palette
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette[open]", timeout=SHORT_TIMEOUT)
        page.locator("#cmd-input").fill("Dashboard")
        page.keyboard.press("Enter")
        page.wait_for_url(server_url, timeout=TIMEOUT)

        # Terminal should still be collapsed (no task to reconnect to)
        terminal_toggle = page.locator("#terminal-toggle")
        assert not terminal_toggle.is_checked(), "Terminal should remain collapsed after navigation"


class TestContainersPagePause:
    """Test containers page auto-refresh pause mechanism.

    The containers page auto-refreshes every 3 seconds. When a user opens
    an action dropdown, refresh should pause to prevent the dropdown from
    closing unexpectedly.
    """

    # Mock HTML for container rows with action dropdowns
    MOCK_ROWS_HTML = """
<tr>
<td>1</td>
<td data-sort="plex"><a href="/stack/plex" class="link">plex</a></td>
<td data-sort="server">server</td>
<td><div class="dropdown dropdown-end">
<label tabindex="0" class="btn btn-circle btn-ghost btn-xs"><svg class="h-4 w-4"></svg></label>
<ul tabindex="0" class="dropdown-content menu menu-sm bg-base-200 rounded-box shadow-lg w-36 z-50 p-2">
<li><a hx-post="/api/stack/plex/restart">Restart</a></li>
</ul>
</div></td>
<td data-sort="nas"><span class="badge">nas</span></td>
<td data-sort="nginx:latest"><code>nginx:latest</code></td>
<td data-sort="running"><span class="badge badge-success">running</span></td>
<td data-sort="3600">1 hour</td>
<td data-sort="5"><progress class="progress" value="5" max="100"></progress><span>5%</span></td>
<td data-sort="104857600"><progress class="progress" value="10" max="100"></progress><span>100MB</span></td>
<td data-sort="1000">↓1KB ↑1KB</td>
</tr>
<tr>
<td>2</td>
<td data-sort="redis"><a href="/stack/redis" class="link">redis</a></td>
<td data-sort="redis">redis</td>
<td><div class="dropdown dropdown-end">
<label tabindex="0" class="btn btn-circle btn-ghost btn-xs"><svg class="h-4 w-4"></svg></label>
<ul tabindex="0" class="dropdown-content menu menu-sm bg-base-200 rounded-box shadow-lg w-36 z-50 p-2">
<li><a hx-post="/api/stack/redis/restart">Restart</a></li>
</ul>
</div></td>
<td data-sort="nas"><span class="badge">nas</span></td>
<td data-sort="redis:7"><code>redis:7</code></td>
<td data-sort="running"><span class="badge badge-success">running</span></td>
<td data-sort="7200">2 hours</td>
<td data-sort="1"><progress class="progress" value="1" max="100"></progress><span>1%</span></td>
<td data-sort="52428800"><progress class="progress" value="5" max="100"></progress><span>50MB</span></td>
<td data-sort="500">↓500B ↑500B</td>
</tr>
"""

    def test_dropdown_pauses_refresh(self, page: Page, server_url: str) -> None:
        """Opening action dropdown pauses auto-refresh.

        Bug: focusin event triggers pause, but focusout fires shortly after
        when focus moves within the dropdown, causing refresh to resume
        while dropdown is still visually open.
        """
        # Mock container rows and update checks
        page.route(
            "**/api/containers/rows/*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=self.MOCK_ROWS_HTML,
            ),
        )
        page.route(
            "**/api/containers/check-updates",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"results": []}',
            ),
        )

        page.goto(f"{server_url}/live-stats")

        # Wait for container rows to load
        page.wait_for_function(
            "document.querySelectorAll('#container-rows tr:not(.loading-row)').length > 0",
            timeout=TIMEOUT,
        )

        # Wait for timer to start
        page.wait_for_function(
            "document.getElementById('refresh-timer')?.textContent?.includes('↻')",
            timeout=TIMEOUT,
        )

        # Click on a dropdown to open it
        dropdown_label = page.locator(".dropdown label").first
        dropdown_label.click()

        # Wait a moment for focusin to trigger
        page.wait_for_timeout(200)

        # Verify pause is engaged
        timer_text = page.locator("#refresh-timer").inner_text()

        assert timer_text == "❚❚", (
            f"Refresh should be paused after clicking dropdown. timer='{timer_text}'"
        )
        assert "❚❚" in timer_text, f"Timer should show pause icon, got '{timer_text}'"

    def test_refresh_stays_paused_while_dropdown_open(self, page: Page, server_url: str) -> None:
        """Refresh remains paused for duration dropdown is open (>5s refresh interval).

        This is the critical test for the pause bug: refresh should stay paused
        for longer than the 3-second refresh interval while dropdown is open.
        """
        # Mock container rows and update checks
        page.route(
            "**/api/containers/rows/*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=self.MOCK_ROWS_HTML,
            ),
        )
        page.route(
            "**/api/containers/check-updates",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"results": []}',
            ),
        )

        page.goto(f"{server_url}/live-stats")

        # Wait for container rows to load
        page.wait_for_function(
            "document.querySelectorAll('#container-rows tr:not(.loading-row)').length > 0",
            timeout=TIMEOUT,
        )

        # Wait for timer to start
        page.wait_for_function(
            "document.getElementById('refresh-timer')?.textContent?.includes('↻')",
            timeout=TIMEOUT,
        )

        # Record a marker in the first row to detect if refresh happened
        page.evaluate("""
            const firstRow = document.querySelector('#container-rows tr');
            if (firstRow) firstRow.dataset.testMarker = 'original';
        """)

        # Click dropdown to pause
        dropdown_label = page.locator(".dropdown label").first
        dropdown_label.click()
        page.wait_for_timeout(200)

        # Confirm paused
        assert page.locator("#refresh-timer").inner_text() == "❚❚"

        # Wait longer than the 5-second refresh interval
        page.wait_for_timeout(6000)

        # Check if still paused
        timer_text = page.locator("#refresh-timer").inner_text()

        # Check if the row was replaced (marker would be gone)
        marker = page.evaluate("""
            document.querySelector('#container-rows tr')?.dataset?.testMarker
        """)

        assert timer_text == "❚❚", f"Refresh should still be paused after 6s. timer='{timer_text}'"
        assert marker == "original", (
            "Table was refreshed while dropdown was open - pause mechanism failed"
        )

    def test_refresh_resumes_after_dropdown_closes(self, page: Page, server_url: str) -> None:
        """Refresh resumes after dropdown is closed."""
        # Mock container rows and update checks
        page.route(
            "**/api/containers/rows/*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=self.MOCK_ROWS_HTML,
            ),
        )
        page.route(
            "**/api/containers/check-updates",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"results": []}',
            ),
        )

        page.goto(f"{server_url}/live-stats")

        # Wait for container rows to load
        page.wait_for_function(
            "document.querySelectorAll('#container-rows tr:not(.loading-row)').length > 0",
            timeout=TIMEOUT,
        )

        # Wait for timer to start
        page.wait_for_function(
            "document.getElementById('refresh-timer')?.textContent?.includes('↻')",
            timeout=TIMEOUT,
        )

        # Click dropdown to pause
        dropdown_label = page.locator(".dropdown label").first
        dropdown_label.click()
        page.wait_for_timeout(200)

        assert page.locator("#refresh-timer").inner_text() == "❚❚"

        # Close dropdown by pressing Escape or clicking elsewhere
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)  # Wait for focusout timeout (150ms) + buffer

        # Verify refresh resumed
        timer_text = page.locator("#refresh-timer").inner_text()

        assert timer_text != "❚❚", (
            f"Refresh should resume after closing dropdown. timer='{timer_text}'"
        )
        assert "↻" in timer_text, f"Timer should show countdown, got '{timer_text}'"
