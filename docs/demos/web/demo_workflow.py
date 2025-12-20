"""Demo: Full workflow.

Records a ~45 second demo combining multiple features:
- Dashboard overview with stats
- Sidebar filtering
- Service navigation
- Terminal streaming
- Theme switching

Run: pytest docs/demos/web/demo_workflow.py -v --no-cov
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import open_command_palette, pause, slow_type, wait_for_sidebar

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _demo_dashboard_and_filter(page: Page, server_url: str) -> None:
    """Demo part 1: Dashboard overview and sidebar filtering."""
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 1500)

    stats_cards = page.locator("#stats-cards .card")
    if stats_cards.count() > 0:
        stats_cards.first.hover()
        pause(page, 800)

    filter_input = page.locator("#sidebar-filter")
    filter_input.click()
    pause(page, 300)
    slow_type(page, "#sidebar-filter", "jelly", delay=150)
    filter_input.dispatch_event("keyup")
    pause(page, 1000)


def _demo_service_and_logs(page: Page) -> None:
    """Demo part 2: Navigate to service and view logs."""
    page.locator("#sidebar-services a", has_text="jellyfin").click()
    page.wait_for_url("**/service/jellyfin", timeout=5000)
    pause(page, 1500)

    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "logs", delay=150)
    pause(page, 500)
    page.keyboard.press("Enter")
    pause(page, 300)

    page.wait_for_selector("#terminal-output .xterm", timeout=5000)
    pause(page, 3000)


def _demo_theme_and_return(page: Page, server_url: str) -> None:
    """Demo part 3: Switch theme and return to dashboard."""
    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "theme: luxury", delay=100)
    pause(page, 600)
    page.keyboard.press("Enter")
    pause(page, 1500)

    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "dash", delay=150)
    pause(page, 500)
    page.keyboard.press("Enter")
    page.wait_for_url(server_url, timeout=5000)
    pause(page, 1500)

    page.locator("#sidebar-filter").fill("")
    page.locator("#sidebar-filter").dispatch_event("keyup")
    pause(page, 1000)

    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 300)
    page.locator("#cmd-input").fill("theme: dark")
    pause(page, 500)
    page.keyboard.press("Enter")
    pause(page, 1000)


@pytest.mark.browser  # type: ignore[misc]
def test_demo_workflow(recording_page: Page, server_url: str) -> None:
    """Record full workflow demo."""
    page = recording_page

    _demo_dashboard_and_filter(page, server_url)
    _demo_service_and_logs(page)
    _demo_theme_and_return(page, server_url)
