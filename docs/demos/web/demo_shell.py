"""Demo: Container shell exec.

Records a ~25 second demo showing:
- Navigating to a stack page
- Clicking Shell button on a container
- Running top command inside the container

Run: pytest docs/demos/web/demo_shell.py -v --no-cov
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
def test_demo_shell(recording_page: Page, server_url: str) -> None:
    """Record container shell demo."""
    page = recording_page

    # Start on dashboard
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 800)

    # Navigate to a stack with a running container (grocy)
    page.locator("#sidebar-stacks a", has_text="grocy").click()
    page.wait_for_url("**/stack/grocy", timeout=5000)
    pause(page, 1500)

    # Wait for containers list to load (loaded via HTMX)
    page.wait_for_selector("#containers-list button", timeout=10000)
    pause(page, 800)

    # Click Shell button on the first container
    shell_btn = page.locator("#containers-list button", has_text="Shell").first
    shell_btn.click()
    pause(page, 1000)

    # Wait for exec terminal to appear
    page.wait_for_selector("#exec-terminal .xterm", timeout=10000)

    # Scroll down to make the terminal visible
    page.locator("#exec-terminal").scroll_into_view_if_needed()
    pause(page, 2000)

    # Run top command
    slow_type(page, "#exec-terminal .xterm-helper-textarea", "top", delay=100)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 4000)  # Let top run for a bit

    # Press q to quit top
    page.keyboard.press("q")
    pause(page, 1000)

    # Run another command to show it's interactive
    slow_type(page, "#exec-terminal .xterm-helper-textarea", "ps aux | head", delay=60)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 2000)
