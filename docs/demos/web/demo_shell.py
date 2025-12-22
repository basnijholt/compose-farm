"""Demo: Container shell exec via command palette.

Records a ~35 second demo showing:
- Navigating to immich stack (multiple containers)
- Using command palette with fuzzy matching ("sh mach") to open shell
- Running a command
- Using command palette to switch to server container shell
- Running another command

Run: pytest docs/demos/web/demo_shell.py -v --no-cov
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
def test_demo_shell(recording_page: Page, server_url: str) -> None:
    """Record container shell demo."""
    page = recording_page

    # Start on dashboard
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 800)

    # Navigate to immich via command palette (has multiple containers)
    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "immich", delay=100)
    pause(page, 600)
    page.keyboard.press("Enter")
    page.wait_for_url("**/stack/immich", timeout=5000)
    pause(page, 1500)

    # Wait for containers list to load (so shell commands are available)
    page.wait_for_selector("#containers-list button", timeout=10000)
    pause(page, 800)

    # Use command palette with fuzzy matching: "sh mach" -> "Shell: immich-machine-learning"
    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "sh mach", delay=100)
    pause(page, 600)
    page.keyboard.press("Enter")
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

    # Blur the terminal to release focus (won't scroll)
    page.evaluate("document.activeElement?.blur()")
    pause(page, 500)

    # Use command palette to switch to server container: "sh serv" -> "Shell: immich-server"
    open_command_palette(page)
    pause(page, 400)
    slow_type(page, "#cmd-input", "sh serv", delay=100)
    pause(page, 600)
    page.keyboard.press("Enter")
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
