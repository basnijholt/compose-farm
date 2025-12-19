# Browser Test Implementation Plan

This is a living document tracking the implementation of comprehensive browser tests for all JavaScript functionality. Delete this file before merging.

## Overview

**Goal:** Ensure all JavaScript interactions have behavioral tests to catch regressions.

**Current state:** 38 tests covering sidebar, command palette, HTMX navigation, action buttons, keyboard shortcuts, and content stability.

**Gap:** Terminal streaming, exec terminals, console page, Monaco editor behaviors, and service-specific command palette actions are untested.

---

## Test Implementation Checklist

### P0: Critical Path (Must Have)

#### Terminal Streaming (`initTerminal`, WebSocket handlers)
Core functionality for showing command output.

- [ ] `test_action_triggers_terminal_websocket_connection`
  - Click action button → terminal connects to `/ws/terminal/{task_id}`
  - Verify WebSocket URL matches task_id from response

- [ ] `test_terminal_displays_streamed_output`
  - Mock WebSocket to send test output
  - Verify output appears in terminal element

- [ ] `test_terminal_shows_done_message_on_completion`
  - Mock WebSocket to send `[Done]`
  - Verify terminal shows completion status

- [ ] `test_terminal_shows_failed_message_on_error`
  - Mock WebSocket to send `[Failed]`
  - Verify terminal shows error status

- [ ] `test_terminal_reconnects_from_localstorage_on_refresh`
  - Trigger action → store task in localStorage
  - Reload page → verify terminal reconnects
  - Note: May need to mock the WebSocket endpoint

#### Console Page - Terminal
Shell access to hosts.

- [ ] `test_console_page_renders`
  - Navigate to `/console`
  - Verify host selector, terminal, and editor sections visible

- [ ] `test_console_host_selector_shows_hosts`
  - Verify dropdown contains hosts from config

- [ ] `test_console_connect_creates_terminal`
  - Select host → click Connect
  - Verify terminal element is created

- [ ] `test_console_terminal_connects_websocket`
  - Click Connect → verify WebSocket to `/ws/shell/{host}`

- [ ] `test_console_shows_connection_status`
  - Connect → verify status shows "Connected to {host}"
  - Disconnect → verify status shows "Disconnected"

#### Console Page - File Editor
Remote file editing.

- [ ] `test_console_editor_loads`
  - Navigate to console → verify Monaco editor initializes

- [ ] `test_console_load_file_fetches_content`
  - Enter path → click Open
  - Mock API response → verify content in editor

- [ ] `test_console_load_file_shows_status`
  - Load file → verify status shows "Loaded: {path}"

- [ ] `test_console_save_file_sends_content`
  - Load file → edit → click Save
  - Verify PUT request with content

- [ ] `test_console_save_file_shows_status`
  - Save → verify status shows "Saved: {path}"

- [ ] `test_console_file_path_detects_language`
  - Load `.yaml` file → verify YAML syntax highlighting
  - Load `.json` file → verify JSON syntax highlighting

---

### P1: Important (Should Have)

#### Exec Terminal (`initExecTerminal`)
Interactive container shell access.

- [ ] `test_container_shell_button_visible`
  - Navigate to service page with containers
  - Verify Shell button appears for running containers

- [ ] `test_shell_button_opens_exec_terminal`
  - Click Shell button → verify exec terminal container becomes visible

- [ ] `test_exec_terminal_connects_websocket`
  - Click Shell → verify WebSocket to `/ws/exec/{service}/{container}/{host}`

- [ ] `test_exec_terminal_cleanup_on_reconnect`
  - Open shell → open another shell
  - Verify first WebSocket closed, no errors

#### Monaco Editor Loading

- [ ] `test_monaco_lazy_loads_on_first_editor`
  - Navigate to page with editor
  - Verify Monaco script loaded from CDN

- [ ] `test_monaco_editor_cmd_s_saves`
  - Focus editor → press Cmd+S
  - Verify save API called

- [ ] `test_multiple_editors_share_monaco_instance`
  - Page with multiple editors → verify Monaco loaded once

#### Service Page Command Palette

- [ ] `test_service_page_palette_shows_service_actions`
  - Navigate to service page → open palette
  - Verify Up, Down, Restart, Pull, Update, Logs commands present

- [ ] `test_palette_service_action_triggers_api`
  - On service page → palette → select Up
  - Verify POST to `/api/service/{name}/up`

- [ ] `test_palette_dashboard_action_from_service_page`
  - On service page → palette → select Apply
  - Verify navigates to dashboard + triggers action

---

### P2: Nice to Have

#### Terminal Resize

- [ ] `test_terminal_resize_observer_fires`
  - Resize terminal container
  - Verify fit() called (terminal dimensions change)

- [ ] `test_exec_terminal_sends_resize_message`
  - Resize exec terminal
  - Verify WebSocket receives resize JSON

#### Terminal Cleanup

- [ ] `test_terminal_dispose_disconnects_observer`
  - Create terminal → dispose
  - Verify no memory leaks (ResizeObserver disconnected)

#### Language Detection

- [ ] `test_language_detection_yaml`
- [ ] `test_language_detection_json`
- [ ] `test_language_detection_python`
- [ ] `test_language_detection_unknown_defaults_plaintext`

#### FAB Animation

- [ ] `test_fab_intro_animation_plays`
  - Page load → verify FAB has animation class

---

## Implementation Notes

### Challenges

1. **WebSocket Testing**: Playwright can intercept HTTP but WebSocket interception is trickier. Options:
   - Use `page.route()` to mock the WebSocket upgrade request (returns mock data)
   - Create a real WebSocket endpoint in the test server that returns predictable data
   - Use Playwright's `page.evaluate()` to mock the WebSocket constructor

2. **Monaco Editor**: Loads from CDN, takes time to initialize. Need:
   - Wait for `window.monaco` to be defined
   - Use `page.wait_for_function()` with appropriate timeout

3. **Terminal Content**: xterm.js renders to canvas, not DOM text. Options:
   - Check for terminal container visibility
   - Use `term.buffer` to read content (requires page.evaluate)
   - Look for specific DOM elements that indicate state

4. **localStorage**: Need to verify task persistence. Options:
   - Use `page.evaluate()` to check localStorage
   - Create task → reload → verify reconnection behavior

### Test Fixtures Needed

```python
@pytest.fixture
def mock_websocket_task():
    """Mock WebSocket that sends predictable task output."""
    # Return a handler that can be used with page.route()
    pass

@pytest.fixture
def mock_console_api():
    """Mock console file read/write API."""
    pass
```

### File Structure

```
tests/web/
├── conftest.py                 # Existing fixtures
├── test_htmx_browser.py        # Existing 38 tests
├── test_terminal_browser.py    # NEW: Terminal streaming tests
├── test_console_browser.py     # NEW: Console page tests
└── test_editor_browser.py      # NEW: Monaco editor tests
```

Or add to existing file as new test classes:
```python
# In test_htmx_browser.py
class TestTerminalStreaming:
    ...

class TestConsolePage:
    ...

class TestMonacoEditor:
    ...

class TestExecTerminal:
    ...
```

---

## Progress Log

### Session 1: Initial Setup
- [x] Created this planning document
- [ ] Implement P0 terminal streaming tests
- [ ] Implement P0 console page tests

---

## Open Questions

1. **WebSocket mocking strategy**: Should we mock at the Playwright level or create test endpoints in the server?
   - Recommendation: Start with test endpoints, fall back to Playwright mocking if needed

2. **Test file organization**: Single file or split by feature?
   - Recommendation: Add to existing file as new test classes for now, split later if it gets too large

3. **How to verify terminal content**: Canvas-based rendering makes text verification hard
   - Recommendation: Focus on behavioral tests (connection, visibility) rather than content verification

---

## Definition of Done

- [ ] All P0 tests passing
- [ ] All P1 tests passing
- [ ] Tests run in CI (nix-shell or playwright install)
- [ ] No flaky tests (run 3x to verify)
- [ ] This document deleted
