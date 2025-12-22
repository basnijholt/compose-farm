"""Demo: Container shell exec.

Records a ~35 second demo showing:
- Navigating to immich stack (multiple containers)
- Opening shell in machine-learning container
- Running a command
- Switching to shell in server container
- Running another command

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

    # Navigate to immich (has multiple containers)
    page.locator("#sidebar-stacks a", has_text="immich").click()
    page.wait_for_url("**/stack/immich", timeout=5000)
    pause(page, 1500)

    # Wait for containers list to load
    page.wait_for_selector("#containers-list button", timeout=10000)
    pause(page, 800)

    # Click Shell button on machine-learning container
    ml_row = page.locator("#containers-list tr", has_text="machine-learning")
    ml_row.locator('[data-tip="Open shell"]').click()
    pause(page, 1000)

    # Wait for exec terminal to appear
    page.wait_for_selector("#exec-terminal .xterm", timeout=10000)

    # Smoothly scroll down to make the terminal visible
    page.evaluate("""
        const terminal = document.getElementById('exec-terminal');
        if (terminal) {
            terminal.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    """)
    pause(page, 1200)

    # Run python version command
    slow_type(page, "#exec-terminal .xterm-helper-textarea", "python3 --version", delay=60)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 1500)

    # Scroll back up to containers list
    page.evaluate("""
        const containers = document.getElementById('containers-list');
        if (containers) {
            containers.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    """)
    pause(page, 1200)

    # Click Shell button on server container
    server_row = page.locator("#containers-list tr", has_text="immich_server")
    server_row.locator('[data-tip="Open shell"]').click()
    pause(page, 1000)

    # Wait for new terminal
    page.wait_for_selector("#exec-terminal .xterm", timeout=10000)

    # Scroll to terminal
    page.evaluate("""
        const terminal = document.getElementById('exec-terminal');
        if (terminal) {
            terminal.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    """)
    pause(page, 1200)

    # Run ls command
    slow_type(page, "#exec-terminal .xterm-helper-textarea", "ls /usr/src/app", delay=60)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 2000)
