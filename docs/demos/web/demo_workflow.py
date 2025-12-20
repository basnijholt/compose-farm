"""Demo: Full workflow.

Records a comprehensive demo (~60 seconds) combining all major features:
1. Console page: terminal with fastfetch, cf pull command
2. Editor showing Compose Farm YAML config
3. Command palette navigation to a service
4. Service actions: up, logs, pull
5. Dashboard overview
6. Theme cycling via command palette

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

    # Navigate to Console page via sidebar menu
    page.locator(".menu a", has_text="Console").click()
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

    # Run cf pull on a service to show Compose Farm in action
    slow_type(page, "#console-terminal .xterm-helper-textarea", "cf pull grocy", delay=60)
    pause(page, 200)
    page.keyboard.press("Enter")
    pause(page, 3000)  # Wait for pull output


def _demo_config_editor(page: Page) -> None:
    """Demo part 2: Show the Compose Farm config in editor."""
    # Scroll down to show the Editor section
    editor_section = page.locator(".collapse", has_text="Editor").first
    editor_section.scroll_into_view_if_needed()
    pause(page, 600)

    # Wait for Monaco editor to load with config content
    page.wait_for_selector("#console-editor .monaco-editor", timeout=10000)
    pause(page, 2000)  # Let viewer see the Compose Farm config file


def _demo_service_actions(page: Page) -> None:
    """Demo part 3: Navigate to service and run actions."""
    # Navigate to service via sidebar (since terminal has keyboard focus)
    # Click on sidebar first to take focus away from terminal
    page.locator("#sidebar-services a", has_text="grocy").click()
    page.wait_for_url("**/service/grocy", timeout=5000)
    pause(page, 1000)

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

    # Run pull
    open_command_palette(page)
    pause(page, 300)
    slow_type(page, "#cmd-input", "pull", delay=100)
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
    pause(page, 1200)

    # Open theme picker and cycle through themes quickly
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 400)

    # Quickly arrow through many themes to show the variety
    for _ in range(8):
        page.keyboard.press("ArrowDown")
        pause(page, 250)

    # Go back up a few
    for _ in range(3):
        page.keyboard.press("ArrowUp")
        pause(page, 250)

    # Select current theme
    page.keyboard.press("Enter")
    pause(page, 800)

    # Open theme picker again and search for a specific theme
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 300)
    slow_type(page, "#cmd-input", " luxury", delay=80)
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 800)

    # Switch to another theme
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 300)
    slow_type(page, "#cmd-input", " cupcake", delay=80)
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 800)

    # Return to dark theme
    page.locator("#theme-btn").click()
    page.wait_for_selector("#cmd-palette[open]", timeout=2000)
    pause(page, 300)
    slow_type(page, "#cmd-input", " dark", delay=80)
    pause(page, 400)
    page.keyboard.press("Enter")
    pause(page, 1000)


@pytest.mark.browser  # type: ignore[misc]
def test_demo_workflow(recording_page: Page, server_url: str) -> None:
    """Record full workflow demo."""
    page = recording_page

    _demo_console_terminal(page, server_url)
    _demo_config_editor(page)
    _demo_service_actions(page)
    _demo_dashboard_and_themes(page, server_url)
