"""Demo: Stack actions.

Records a ~30 second demo showing:
- Navigating to a stack page
- Viewing compose file in Monaco editor
- Triggering Restart action via command palette
- Watching terminal output stream
- Triggering Logs action

Run: pytest docs/demos/web/demo_stack.py -v --no-cov
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
def test_demo_stack(recording_page: Page, server_url: str) -> None:
    """Record stack actions demo."""
    page = recording_page

    # Start on dashboard
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 800)

    # Navigate to grocy via command palette
    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "grocy", delay=100)
    pause(page, 500)
    page.keyboard.press("Enter")
    page.wait_for_url("**/stack/grocy", timeout=5000)
    pause(page, 1000)  # Show stack page

    # Click on Compose File collapse to show the Monaco editor
    # The collapse uses a checkbox input, click it via the parent collapse div
    compose_collapse = page.locator(".collapse", has_text="Compose File").first
    compose_collapse.locator("input[type=checkbox]").click(force=True)
    pause(page, 500)

    # Wait for Monaco editor to load and show content
    page.wait_for_selector("#compose-editor .monaco-editor", timeout=10000)
    pause(page, 2000)  # Let viewer see the compose file

    # Smoothly scroll down to show more of the editor
    page.evaluate("""
        const editor = document.getElementById('compose-editor');
        if (editor) {
            editor.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    """)
    pause(page, 1200)  # Wait for smooth scroll animation

    # Close the compose file section
    compose_collapse.locator("input[type=checkbox]").click(force=True)
    pause(page, 500)

    # Open command palette for stack actions
    open_command_palette(page)
    pause(page, 400)

    # Filter to Restart action
    slow_type(page, "#cmd-input", "restart", delay=120)
    pause(page, 600)

    # Execute Restart
    page.keyboard.press("Enter")
    pause(page, 300)

    # Wait for terminal to expand and show output
    page.wait_for_selector("#terminal-output .xterm", timeout=5000)
    pause(page, 2500)  # Let viewer see terminal streaming

    # Open palette again for Logs
    open_command_palette(page)
    pause(page, 400)

    # Filter to Logs action
    slow_type(page, "#cmd-input", "logs", delay=120)
    pause(page, 600)

    # Execute Logs
    page.keyboard.press("Enter")
    pause(page, 300)

    # Show log output
    page.wait_for_selector("#terminal-output .xterm", timeout=5000)
    pause(page, 2500)  # Final view of logs
