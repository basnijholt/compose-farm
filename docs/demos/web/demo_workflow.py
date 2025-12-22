"""Demo: Full workflow.

Records a comprehensive demo (~60 seconds) combining all major features:
1. Console page: terminal with fastfetch, cf pull command
2. Editor showing Compose Farm YAML config
3. Command palette navigation to grocy stack
4. Stack actions: up, logs
5. Switch to dozzle stack via command palette, run update
6. Dashboard overview
7. Theme cycling via command palette

This demo is used on the homepage and Web UI page as the main showcase.

Run: pytest docs/demos/web/demo_workflow.py -v --no-cov
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import open_command_palette, pause, slow_type, wait_for_sidebar

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _demo_console_terminal(page: Page, server_url: str) -> None:
    """Demo part 1: Console page with terminal and editor."""
    # Start on dashboard briefly
    page.goto(server_url)
    wait_for_sidebar(page)
    pause(page, 800)

    # Navigate to Console page via command palette
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "cons", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    page.wait_for_url("**/console", timeout=5000)
    pause(page, 800)

    # Wait for terminal to be ready
    page.wait_for_selector("#console-terminal .xterm", timeout=10000)
    pause(page, 1000)

    # Run fastfetch first
    slow_type(page, "#console-terminal .xterm-helper-textarea", "fastfetch", delay=60)
    pause(page, 200)
    page.keyboard.press("Enter")
    pause(page, 2000)  # Wait for output

    # Run cf pull on a stack to show Compose Farm in action
    slow_type(page, "#console-terminal .xterm-helper-textarea", "cf pull grocy", delay=60)
    pause(page, 200)
    page.keyboard.press("Enter")
    pause(page, 3000)  # Wait for pull output


def _demo_config_editor(page: Page) -> None:
    """Demo part 2: Show the Compose Farm config in editor."""
    # Smoothly scroll down to show the Editor section
    # Use JavaScript for smooth scrolling animation
    page.evaluate("""
        const editor = document.getElementById('console-editor');
        if (editor) {
            editor.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    """)
    pause(page, 1200)  # Wait for smooth scroll animation

    # Wait for Monaco editor to load with config content
    page.wait_for_selector("#console-editor .monaco-editor", timeout=10000)
    pause(page, 2000)  # Let viewer see the Compose Farm config file


def _demo_stack_actions(page: Page) -> None:
    """Demo part 3: Navigate to stack and run actions."""
    # Click on sidebar to take focus away from terminal, then use command palette
    page.locator("#sidebar-stacks").click()
    pause(page, 300)

    # Navigate to grocy via command palette
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "grocy", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    page.wait_for_url("**/stack/grocy", timeout=5000)
    pause(page, 1000)

    # Open Compose File editor to show the compose.yaml
    compose_collapse = page.locator(".collapse", has_text="Compose File").first
    compose_collapse.locator("input[type=checkbox]").click(force=True)
    pause(page, 500)

    # Wait for Monaco editor to load and show content
    page.wait_for_selector("#compose-editor .monaco-editor", timeout=10000)
    pause(page, 2000)  # Let viewer see the compose file

    # Close the compose file section
    compose_collapse.locator("input[type=checkbox]").click(force=True)
    pause(page, 500)

    # Run Up action via command palette
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "up", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 200)

    # Wait for terminal output
    page.wait_for_selector("#terminal-output .xterm", timeout=5000)
    pause(page, 2500)

    # Show logs
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "logs", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 200)

    page.wait_for_selector("#terminal-output .xterm", timeout=5000)
    pause(page, 2500)

    # Switch to dozzle via command palette (on nas for lower latency)
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "dozzle", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    page.wait_for_url("**/stack/dozzle", timeout=5000)
    pause(page, 1000)

    # Run update action
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "upda", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 200)

    page.wait_for_selector("#terminal-output .xterm", timeout=5000)
    pause(page, 2500)


def _demo_dashboard_and_themes(page: Page, server_url: str) -> None:
    """Demo part 4: Dashboard and theme cycling."""
    # Navigate to dashboard via command palette
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "dash", delay=100)
    pause(page, 400)
    page.keyboard.press("Enter")
    page.wait_for_url(server_url, timeout=5000)
    pause(page, 800)

    # Scroll to top of page to ensure dashboard is fully visible
    page.evaluate("window.scrollTo(0, 0)")
    pause(page, 600)

    # Open theme picker and arrow down to Dracula (shows live preview)
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 400)

    # Arrow down through themes with live preview until we reach Dracula
    for _ in range(19):
        page.keyboard.press("ArrowDown")
        pause(page, 180)

    # Select Dracula theme and end on it
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 1500)


@pytest.mark.browser  # type: ignore[misc]
def test_demo_workflow(recording_page: Page, server_url: str) -> None:
    """Record full workflow demo."""
    page = recording_page

    _demo_console_terminal(page, server_url)
    _demo_config_editor(page)
    _demo_stack_actions(page)
    _demo_dashboard_and_themes(page, server_url)
