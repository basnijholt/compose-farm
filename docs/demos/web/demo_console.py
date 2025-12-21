"""Demo: Console terminal.

Records a ~30 second demo showing:
- Navigating to Console page
- Running cf commands in the terminal
- Showing the Compose Farm config in Monaco editor

Run: pytest docs/demos/web/demo_console.py -v --no-cov
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
def test_demo_console(recording_page: Page, server_url: str) -> None:
    """Record console terminal demo."""
    page = recording_page

    # Start on dashboard
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 800)

    # Navigate to Console page via sidebar menu
    page.locator(".menu a", has_text="Console").click()
    page.wait_for_url("**/console", timeout=5000)
    pause(page, 1000)

    # Wait for terminal to be ready (auto-connects)
    page.wait_for_selector("#console-terminal .xterm", timeout=10000)
    pause(page, 1500)

    # Run fastfetch first
    slow_type(page, "#console-terminal .xterm-helper-textarea", "fastfetch", delay=80)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 2500)  # Wait for output

    # Type cf stats command
    slow_type(page, "#console-terminal .xterm-helper-textarea", "cf stats", delay=80)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 3000)  # Wait for output

    # Type cf ps command
    slow_type(page, "#console-terminal .xterm-helper-textarea", "cf ps grocy", delay=80)
    pause(page, 300)
    page.keyboard.press("Enter")
    pause(page, 2500)  # Wait for output

    # Smoothly scroll down to show the Editor section with Compose Farm config
    page.evaluate("""
        const editor = document.getElementById('console-editor');
        if (editor) {
            editor.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    """)
    pause(page, 1200)  # Wait for smooth scroll animation

    # Wait for Monaco editor to load with config content
    page.wait_for_selector("#console-editor .monaco-editor", timeout=10000)
    pause(page, 2500)  # Let viewer see the Compose Farm config file

    # Final pause
    pause(page, 800)
