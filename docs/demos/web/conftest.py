"""Shared fixtures for web UI demo recordings.

Based on tests/web/test_htmx_browser.py patterns for consistency.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import uvicorn

from compose_farm.config import Config as CFConfig
from compose_farm.config import load_config
from compose_farm.state import load_state as _original_load_state
from compose_farm.web.app import create_app
from compose_farm.web.cdn import CDN_ASSETS, ensure_vendor_cache

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import BrowserContext, Page, Route

# Stacks to exclude from demo recordings (exact match)
DEMO_EXCLUDE_STACKS = {"arr"}


def _get_filtered_config() -> CFConfig:
    """Load config but filter out excluded stacks."""
    config = load_config()
    filtered_stacks = {
        name: host for name, host in config.stacks.items() if name not in DEMO_EXCLUDE_STACKS
    }
    return CFConfig(
        compose_dir=config.compose_dir,
        hosts=config.hosts,
        stacks=filtered_stacks,
        traefik_file=config.traefik_file,
        traefik_stack=config.traefik_stack,
        config_path=config.config_path,
    )


def _get_filtered_state(config: CFConfig) -> dict[str, str | list[str]]:
    """Load state but filter out excluded stacks."""
    state = _original_load_state(config)
    return {name: host for name, host in state.items() if name not in DEMO_EXCLUDE_STACKS}


@pytest.fixture(scope="session")
def vendor_cache(request: pytest.FixtureRequest) -> Path:
    """Download CDN assets once and cache to disk for faster recordings."""
    cache_dir = Path(str(request.config.rootdir)) / ".pytest_cache" / "vendor"
    return ensure_vendor_cache(cache_dir)


@pytest.fixture(scope="session")
def browser_type_launch_args() -> dict[str, str]:
    """Configure Playwright to use system Chromium if available."""
    for name in ["chromium", "chromium-browser", "google-chrome", "chrome"]:
        path = shutil.which(name)
        if path:
            return {"executable_path": path}
    return {}


# Path to real compose-farm config
REAL_CONFIG_PATH = Path("/opt/stacks/compose-farm.yaml")


@pytest.fixture(scope="module")
def server_url() -> Generator[str, None, None]:
    """Start demo server using real config (with filtered stacks) and return URL."""
    os.environ["CF_CONFIG"] = str(REAL_CONFIG_PATH)

    # Patch at source module level so all callers get filtered versions
    patches = [
        # Patch load_state at source - all functions calling it get filtered state
        patch("compose_farm.state.load_state", _get_filtered_state),
        # Patch get_config where imported
        patch("compose_farm.web.routes.pages.get_config", _get_filtered_config),
        patch("compose_farm.web.routes.api.get_config", _get_filtered_config),
        patch("compose_farm.web.routes.actions.get_config", _get_filtered_config),
        patch("compose_farm.web.app.get_config", _get_filtered_config),
        patch("compose_farm.web.ws.get_config", _get_filtered_config),
    ]

    for p in patches:
        p.start()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    app = create_app()
    uvicorn_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(uvicorn_config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    server_ready = False
    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=0.5)  # noqa: S310
            server_ready = True
            break
        except Exception:
            time.sleep(0.1)

    if not server_ready:
        msg = f"Demo server failed to start on {url}"
        raise RuntimeError(msg)

    yield url

    server.should_exit = True
    thread.join(timeout=2)
    os.environ.pop("CF_CONFIG", None)

    for p in patches:
        p.stop()


@pytest.fixture(scope="module")
def recording_output_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Directory for video recordings."""
    return Path(tmp_path_factory.mktemp("recordings"))


@pytest.fixture
def recording_context(
    browser: Any,  # pytest-playwright's browser fixture
    vendor_cache: Path,
    recording_output_dir: Path,
) -> Generator[BrowserContext, None, None]:
    """Browser context with video recording enabled."""
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        record_video_dir=str(recording_output_dir),
        record_video_size={"width": 1280, "height": 720},
    )

    # Set up CDN interception
    cache = {url: (vendor_cache / f, ct) for url, (f, ct) in CDN_ASSETS.items()}

    def handle_cdn(route: Route) -> None:
        url = route.request.url
        for url_prefix, (filepath, content_type) in cache.items():
            if url.startswith(url_prefix):
                route.fulfill(status=200, content_type=content_type, body=filepath.read_bytes())
                return
        route.abort("failed")

    context.route(re.compile(r"https://(cdn\.jsdelivr\.net|unpkg\.com)/.*"), handle_cdn)

    yield context
    context.close()


@pytest.fixture
def recording_page(recording_context: BrowserContext) -> Generator[Page, None, None]:
    """Page with recording and slow motion enabled."""
    page = recording_context.new_page()
    yield page
    page.close()


# Demo helper functions


def pause(page: Page, ms: int = 500) -> None:
    """Pause for visibility in recording."""
    page.wait_for_timeout(ms)


def slow_type(page: Page, selector: str, text: str, delay: int = 100) -> None:
    """Type with visible delay between keystrokes."""
    page.type(selector, text, delay=delay)


def open_command_palette(page: Page) -> None:
    """Open command palette with Ctrl+K."""
    page.keyboard.press("Control+k")
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 300)


def close_command_palette(page: Page) -> None:
    """Close command palette with Escape."""
    page.keyboard.press("Escape")
    page.wait_for_selector("#cmd-palette:not([open])", timeout=2000)
    pause(page, 200)


def wait_for_sidebar(page: Page) -> None:
    """Wait for sidebar to load with stacks."""
    page.wait_for_selector("#sidebar-stacks", timeout=5000)
    pause(page, 300)


def navigate_to_stack(page: Page, stack: str) -> None:
    """Navigate to a stack page via sidebar click."""
    page.locator("#sidebar-stacks a", has_text=stack).click()
    page.wait_for_url(f"**/stack/{stack}", timeout=5000)
    pause(page, 500)


def select_command(page: Page, command: str) -> None:
    """Filter and select a command from the palette."""
    page.locator("#cmd-input").fill(command)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 200)
