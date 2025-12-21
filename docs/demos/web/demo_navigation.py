"""Demo: Command palette navigation.

Records a ~15 second demo showing:
- Opening command palette with Ctrl+K
- Fuzzy search filtering
- Arrow key navigation
- Stack and page navigation

Run: pytest docs/demos/web/demo_navigation.py -v --no-cov
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
def test_demo_navigation(recording_page: Page, server_url: str) -> None:
    """Record command palette navigation demo."""
    page = recording_page

    # Start on dashboard
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 1000)  # Let viewer see dashboard

    # Open command palette with keyboard shortcut
    open_command_palette(page)
    pause(page, 500)

    # Type partial stack name for fuzzy search
    slow_type(page, "#cmd-input", "grocy", delay=120)
    pause(page, 800)

    # Arrow down to show selection movement
    page.keyboard.press("ArrowDown")
    pause(page, 400)
    page.keyboard.press("ArrowUp")
    pause(page, 400)

    # Press Enter to navigate to stack
    page.keyboard.press("Enter")
    page.wait_for_url("**/stack/grocy", timeout=5000)
    pause(page, 1500)  # Show stack page

    # Open palette again to navigate elsewhere
    open_command_palette(page)
    pause(page, 400)

    # Navigate to another stack (immich) to show more navigation
    slow_type(page, "#cmd-input", "imm", delay=120)
    pause(page, 600)
    page.keyboard.press("Enter")
    page.wait_for_url("**/stack/immich", timeout=5000)
    pause(page, 1200)  # Show immich stack page

    # Open palette one more time, navigate back to dashboard
    open_command_palette(page)
    slow_type(page, "#cmd-input", "dashb", delay=120)
    pause(page, 500)
    page.keyboard.press("Enter")
    page.wait_for_url(server_url, timeout=5000)
    pause(page, 1000)  # Final dashboard view
