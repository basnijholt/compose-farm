# Browser Test Implementation Plan

This is a living document tracking the implementation of comprehensive browser tests for all JavaScript functionality. **Delete this file before merging.**

---

## Context

### What is Compose Farm?

Compose Farm is a CLI + Web UI for managing Docker Compose services across multiple SSH hosts. The web UI is a secondary interface built with:
- **Backend**: FastAPI + Jinja2 templates
- **Frontend**: HTMX + DaisyUI (Tailwind) + xterm.js (terminals) + Monaco (editors)
- **No build system**: All JS libraries loaded from CDN

### Relevant File Paths

```
src/compose_farm/web/
├── app.py              # FastAPI app factory
├── deps.py             # Shared config/template helpers
├── streaming.py        # CLI subprocess execution, task registry
├── ws.py               # WebSocket handlers (terminal, exec, shell)
├── routes/
│   ├── pages.py        # HTML page routes
│   ├── api.py          # REST API (file CRUD, container status)
│   └── actions.py      # Action endpoints (up, down, restart, etc.)
├── templates/
│   ├── base.html       # Layout with sidebar
│   ├── index.html      # Dashboard
│   ├── service.html    # Service detail page
│   ├── console.html    # Terminal + file editor (has ~200 lines inline JS)
│   └── partials/       # HTMX partials
└── static/
    └── app.js          # Main JavaScript (~680 lines)

tests/web/
├── conftest.py              # Shared fixtures
├── test_htmx_browser.py     # Browser tests (38 existing tests)
├── test_backup.py           # Unit tests for file backup
├── test_helpers.py          # Unit tests for helpers
└── test_template_context.py # Template rendering tests
```

### How to Run Browser Tests

```bash
# Browser tests require Chromium via nix-shell
nix-shell --run "uv run pytest tests/web/test_htmx_browser.py -v --no-cov"

# Run a specific test class
nix-shell --run "uv run pytest tests/web/test_htmx_browser.py::TestCommandPalette -v --no-cov"

# Run with headed browser for debugging
nix-shell --run "uv run pytest tests/web/test_htmx_browser.py::TestSidebarFilter -v --no-cov --headed"
```

---

## JavaScript Functionality Overview

### app.js Functions

| Function | Purpose | WebSocket/API |
|----------|---------|---------------|
| `createTerminal(container, opts, onResize)` | Create xterm.js terminal with ResizeObserver | - |
| `createWebSocket(path)` | Create WebSocket with correct protocol | - |
| `whenXtermReady(callback)` | Wait for xterm.js CDN to load | - |
| `initTerminal(elementId, taskId)` | Connect to task streaming WebSocket | `/ws/terminal/{task_id}` |
| `initExecTerminal(service, container, host)` | Connect to container exec WebSocket | `/ws/exec/{service}/{container}/{host}` |
| `sidebarFilter()` | Filter sidebar by text/host | - |
| `loadMonaco(callback)` | Lazy load Monaco from CDN | - |
| `createEditor(container, content, lang, opts)` | Create Monaco editor | - |
| `initMonacoEditors()` | Initialize all page editors | - |
| `saveAllEditors()` | Save all editors via PUT requests | `/api/compose`, `/api/env`, `/api/config` |
| `refreshDashboard()` | Dispatch `cf:refresh` custom event | - |
| `expandTerminal()` | Expand terminal collapse section | - |
| `tryReconnectToTask()` | Reconnect to active task from localStorage | `/ws/terminal/{task_id}` |
| Command Palette (IIFE) | Cmd+K navigation, filtering, execution | Various |

### console.html Functions (inline)

| Function | Purpose | API |
|----------|---------|-----|
| `connectConsole()` | Connect to host shell | `/ws/shell/{host}` |
| `loadFile()` | Load file content | `GET /api/console/file?host=X&path=Y` |
| `saveFile()` | Save file content | `PUT /api/console/file?host=X&path=Y` |
| `initConsoleEditor()` | Create Monaco editor for console | - |

### WebSocket Endpoints

| Endpoint | Purpose | Message Format |
|----------|---------|----------------|
| `/ws/terminal/{task_id}` | Stream CLI command output | Server sends text, client receives |
| `/ws/exec/{service}/{container}/{host}` | Interactive container shell | Bidirectional: text + `{type: "resize", cols, rows}` |
| `/ws/shell/{host}` | Interactive host shell | Bidirectional: text + `{type: "resize", cols, rows}` |

---

## Existing Test Patterns

### Test Server Fixture

The test server uses a module-scoped fixture that:
1. Creates a temp config with test services (plex, sonarr, radarr, jellyfin)
2. Patches `get_config()` to return the test config
3. Starts uvicorn in a thread
4. Returns the server URL

```python
@pytest.fixture(scope="module")
def server_url(test_config: Path, monkeypatch_module: pytest.MonkeyPatch) -> Generator[str, None, None]:
    config = load_config(test_config)
    monkeypatch_module.setattr(web_deps, "get_config", lambda: config)
    # ... start server, yield URL, cleanup
```

### Typical Test Pattern

```python
class TestSidebarFilter:
    def test_text_filter_hides_non_matching_services(self, page: Page, server_url: str) -> None:
        """Typing in filter input hides services that don't match."""
        page.goto(server_url)
        page.wait_for_selector("#sidebar-services", timeout=5000)

        # Initial state
        visible_items = page.locator("#sidebar-services li:not([hidden])")
        assert visible_items.count() == 4

        # Trigger action
        filter_input = page.locator("#sidebar-filter")
        filter_input.fill("plex")
        filter_input.dispatch_event("keyup")

        # Verify result
        visible_after = page.locator("#sidebar-services li:not([hidden])")
        assert visible_after.count() == 1
```

### Mocking API Responses

```python
def test_apply_button_makes_post_request(self, page: Page, server_url: str) -> None:
    page.goto(server_url)

    api_calls: list[str] = []

    def handle_route(route: Route) -> None:
        api_calls.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"task_id": "test-123"}',
        )

    page.route("**/api/apply", handle_route)
    page.locator("button", has_text="Apply").click()
    page.wait_for_timeout(500)

    assert len(api_calls) == 1
```

### Waiting for Dynamic Content

```python
# Wait for HTMX to load content
page.wait_for_selector("#sidebar-services", timeout=5000)

# Wait for Monaco to load
page.wait_for_function("typeof monaco !== 'undefined'", timeout=10000)

# Wait for element state change
page.wait_for_function(
    "document.getElementById('terminal-toggle')?.checked === true",
    timeout=3000,
)
```

---

## Test Implementation Checklist

### P0: Critical Path (Must Have)

#### Terminal Streaming (`initTerminal`, WebSocket handlers)

The core functionality for showing command output after clicking action buttons.

**How it works:**
1. User clicks action button (e.g., "Apply")
2. Server creates task, returns `{task_id: "..."}`
3. JS intercepts response in `htmx:afterRequest`
4. JS calls `initTerminal("terminal-output", taskId)`
5. WebSocket connects to `/ws/terminal/{task_id}`
6. Server streams output, JS writes to terminal
7. When done, server sends `[Done]` or `[Failed]`

**Tests needed:**

- [ ] `test_action_triggers_terminal_websocket_connection`
  - Mock `/api/apply` to return `{task_id: "test-123"}`
  - Intercept WebSocket upgrade request
  - Verify connection to `/ws/terminal/test-123`

- [ ] `test_terminal_expands_on_action_response`
  - Already exists as `test_action_response_expands_terminal`
  - ✅ DONE

- [ ] `test_terminal_displays_connected_message`
  - After WebSocket opens, terminal should show `[Connected]`
  - Use `page.evaluate()` to check terminal buffer or look for text

- [ ] `test_terminal_stores_task_in_localstorage`
  - After action → verify `localStorage.getItem('cf_task:/')` equals task_id

- [ ] `test_terminal_clears_localstorage_on_done`
  - Complete task → verify localStorage cleared

#### Console Page - Terminal

**How it works:**
1. User navigates to `/console`
2. User selects host from dropdown
3. User clicks "Connect"
4. `connectConsole()` creates terminal + WebSocket to `/ws/shell/{host}`
5. Bidirectional communication: keystrokes sent, output received

**Tests needed:**

- [ ] `test_console_page_renders`
  - Navigate to `/console`
  - Verify: host selector, Connect button, terminal container, editor container

- [ ] `test_console_host_selector_shows_all_hosts`
  - Verify dropdown has options for server-1, server-2

- [ ] `test_console_connect_shows_status`
  - Click Connect → status shows "Connecting..." then "Connected to {host}"
  - Note: WebSocket may fail in test (no real SSH), so mock or check initial state

- [ ] `test_console_connect_creates_terminal_element`
  - Click Connect → terminal container has xterm elements (`.xterm` class)

- [ ] `test_console_auto_connects_on_load`
  - Console page auto-connects to first host on load
  - Verify terminal created without clicking Connect

#### Console Page - File Editor

**How it works:**
1. User enters file path (e.g., `~/docker-compose.yaml`)
2. User clicks "Open"
3. `loadFile()` fetches `GET /api/console/file?host=X&path=Y`
4. Content displayed in Monaco editor
5. User edits, clicks "Save"
6. `saveFile()` sends `PUT /api/console/file?host=X&path=Y` with content

**Tests needed:**

- [ ] `test_console_editor_initializes`
  - Navigate to console → Monaco editor loads
  - Wait for `typeof monaco !== 'undefined'`

- [ ] `test_console_load_file_calls_api`
  - Enter path → click Open
  - Verify GET request to `/api/console/file`

- [ ] `test_console_load_file_shows_content`
  - Mock API to return `{success: true, content: "test content"}`
  - Verify editor contains "test content"

- [ ] `test_console_load_file_updates_status`
  - Load file → status shows "Loaded: {path}"

- [ ] `test_console_save_file_calls_api`
  - Load file → click Save
  - Verify PUT request with editor content

- [ ] `test_console_save_file_updates_status`
  - Save file → status shows "Saved: {path}"

---

### P1: Important (Should Have)

#### Exec Terminal (`initExecTerminal`)

Container shell access from service page.

**How it works:**
1. Service page shows container list
2. Each container has "Shell" button
3. Button calls `initExecTerminal(service, container, host)`
4. Creates terminal + WebSocket to `/ws/exec/{service}/{container}/{host}`

**Tests needed:**

- [ ] `test_service_page_has_exec_terminal_container`
  - Navigate to service page → exec terminal container exists (hidden)

- [ ] `test_shell_button_shows_exec_terminal`
  - Note: Requires containers to be visible, which requires mocking container status API
  - Click Shell → exec terminal container becomes visible

- [ ] `test_exec_terminal_connects_websocket`
  - Click Shell → WebSocket connects to correct path

#### Monaco Editor Behaviors

- [ ] `test_dashboard_monaco_loads`
  - Dashboard page → Monaco loads for config editor

- [ ] `test_service_page_monaco_loads`
  - Service page → Monaco loads for compose/env editors

- [ ] `test_editor_cmd_s_triggers_save`
  - Focus editor → Cmd+S → save API called
  - Note: May conflict with global Ctrl+S test

#### Service Page Command Palette

- [ ] `test_service_page_palette_has_action_commands`
  - Navigate to `/service/plex` → open palette
  - Verify: Up, Down, Restart, Pull, Update, Logs commands visible

- [ ] `test_palette_action_triggers_service_api`
  - On service page → palette → select "Up"
  - Verify POST to `/api/service/plex/up`

- [ ] `test_palette_apply_from_service_page`
  - On service page → palette → select "Apply"
  - Verify navigates to dashboard + triggers `/api/apply`

---

### P2: Nice to Have (Lower Priority)

- [ ] `test_terminal_resize_updates_dimensions`
- [ ] `test_exec_terminal_sends_resize_json`
- [ ] `test_language_detection_yaml`
- [ ] `test_language_detection_json`
- [ ] `test_fab_animation_on_load`

---

## Implementation Strategy

### Phase 1: Console Page Tests (Easiest)
Start here because:
- Console page is self-contained
- File API can be easily mocked
- No complex task/streaming state

### Phase 2: Terminal Streaming Tests
- Need to handle WebSocket mocking
- May need real `/ws/terminal` endpoint that returns test data
- Focus on behavioral tests (connection, expansion) not content

### Phase 3: Exec Terminal Tests
- Depends on container status API mocking
- Similar WebSocket challenges as terminal streaming

### Phase 4: Service Page Palette Tests
- Build on existing command palette tests
- Just need to verify service-specific commands

---

## Technical Challenges & Solutions

### Challenge 1: WebSocket Testing

**Problem:** Playwright's `page.route()` intercepts HTTP but WebSocket upgrade is trickier.

**Solutions:**
1. **Intercept upgrade request** - Can detect that WebSocket was attempted
2. **Create test WebSocket endpoint** - Add a test route that returns predictable data
3. **Use page.evaluate()** - Mock `WebSocket` constructor in browser

**Recommended approach:** For P0, verify WebSocket URL is correct via route interception. Don't verify actual content (too complex for now).

### Challenge 2: xterm.js Content

**Problem:** xterm.js renders to canvas, not readable DOM.

**Solutions:**
1. Check terminal visibility and initialization
2. Use `page.evaluate()` to read `term.buffer`
3. Look for status elements (spinner, connected message)

**Recommended approach:** Focus on behavioral tests. Verify terminal is created and connected, not specific output text.

### Challenge 3: Monaco Editor Timing

**Problem:** Monaco loads async from CDN, may not be ready immediately.

**Solution:** Use `page.wait_for_function("typeof monaco !== 'undefined'", timeout=10000)`

---

## Progress Log

| Date | What was done |
|------|---------------|
| 2024-12-19 | Created this planning document |
| | Next: Implement P0 console page tests |

---

## Definition of Done

- [ ] All P0 tests implemented and passing
- [ ] All P1 tests implemented and passing
- [ ] Tests run successfully in nix-shell
- [ ] No flaky tests (verified with 3 consecutive runs)
- [ ] PR updated with test changes
- [ ] This file deleted before merge
