"""Demo: Live Stats page.

Records a ~20 second demo showing:
- Navigating to Live Stats via command palette
- Container table with real-time stats
- Filtering containers
- Sorting by different columns
- Auto-refresh countdown

Run: pytest docs/demos/web/demo_live_stats.py -v --no-cov
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import (
    open_command_palette,
    pause,
    slow_type,
    wait_for_sidebar,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.mark.browser  # type: ignore[misc]
def test_demo_live_stats(wide_recording_page: Page, server_url: str) -> None:
    """Record Live Stats page demo."""
    page = wide_recording_page

    # Start on dashboard
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 1000)

    # Navigate to Live Stats via command palette
    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "live", delay=100)
    pause(page, 500)
    page.keyboard.press("Enter")
    page.wait_for_url("**/live-stats", timeout=5000)

    # Wait for containers to load (may take ~10s on first load due to SSH)
    page.wait_for_selector("#container-rows tr:not(:has(.loading))", timeout=30000)
    pause(page, 2000)  # Let viewer see the full table with timer

    # Demonstrate filtering
    slow_type(page, "#filter-input", "grocy", delay=100)
    pause(page, 1500)  # Show filtered results

    # Clear filter
    page.fill("#filter-input", "")
    pause(page, 1000)

    # Sort by memory (click header)
    page.click("th:has-text('Mem')")
    pause(page, 1500)

    # Sort by CPU
    page.click("th:has-text('CPU')")
    pause(page, 1500)

    # Sort by host
    page.click("th:has-text('Host')")
    pause(page, 1500)

    # Watch auto-refresh timer count down
    pause(page, 3500)  # Wait for refresh to happen

    # Hover on action menu to show pause behavior
    action_btn = page.locator('button[onclick^="openActionMenu"]').first
    action_btn.scroll_into_view_if_needed()
    action_btn.hover()
    pause(page, 2000)  # Show paused state (timer shows ⏸) and action menu

    # Move away to close menu and resume refresh
    page.locator("h2").first.hover()  # Move to header
    pause(page, 3500)  # Watch countdown resume and refresh happen

    # Final pause
    pause(page, 1000)
