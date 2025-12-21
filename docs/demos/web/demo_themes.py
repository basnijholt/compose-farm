"""Demo: Theme switching.

Records a ~15 second demo showing:
- Opening theme picker via theme button
- Live theme preview on arrow navigation
- Selecting different themes
- Theme persistence

Run: pytest docs/demos/web/demo_themes.py -v --no-cov
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import (
    pause,
    slow_type,
    wait_for_sidebar,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.mark.browser  # type: ignore[misc]
def test_demo_themes(recording_page: Page, server_url: str) -> None:
    """Record theme switching demo."""
    page = recording_page

    # Start on dashboard
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 1000)  # Show initial theme

    # Click theme button to open theme picker
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 600)

    # Arrow through many themes to show live preview effect
    for _ in range(12):
        page.keyboard.press("ArrowDown")
        pause(page, 350)  # Show each preview

    # Go back up through a few (land on valentine, not cyberpunk)
    for _ in range(4):
        page.keyboard.press("ArrowUp")
        pause(page, 350)

    # Select current theme with Enter
    page.keyboard.press("Enter")
    pause(page, 1000)

    # Close palette with Escape
    page.keyboard.press("Escape")
    pause(page, 800)

    # Open again and use search to find specific theme
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 400)

    # Type to filter to a light theme (theme button pre-populates "theme:")
    slow_type(page, "#cmd-input", "cup", delay=100)
    pause(page, 500)
    page.keyboard.press("Enter")
    pause(page, 1000)

    # Close and return to dark
    page.keyboard.press("Escape")
    pause(page, 500)
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 300)

    slow_type(page, "#cmd-input", "dark", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 800)
