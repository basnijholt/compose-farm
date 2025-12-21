---
icon: lucide/layout-dashboard
---

# Web UI

Compose Farm includes a web interface for managing stacks from your browser. Start it with:

```bash
cf web
```

Then open [http://localhost:8000](http://localhost:8000).

## Features

### Full Workflow

Console terminal, config editor, stack navigation, actions (up, logs, update), dashboard overview, and theme switching - all in one flow.

<video autoplay loop muted playsinline>
  <source src="/assets/web-workflow.webm" type="video/webm">
</video>

### Stack Actions

Navigate to any stack and use the command palette to trigger actions like restart, pull, update, or view logs. Output streams in real-time via WebSocket.

<video autoplay loop muted playsinline>
  <source src="/assets/web-stack.webm" type="video/webm">
</video>

### Theme Switching

35 themes available via the command palette. Type `theme:` to filter, then use arrow keys to preview themes live before selecting.

<video autoplay loop muted playsinline>
  <source src="/assets/web-themes.webm" type="video/webm">
</video>

### Command Palette

Press `Ctrl+K` (or `Cmd+K` on macOS) to open the command palette. Use fuzzy search to quickly navigate, trigger actions, or change themes.

<video autoplay loop muted playsinline>
  <source src="/assets/web-navigation.webm" type="video/webm">
</video>

## Pages

### Dashboard (`/`)

- Stack overview with status indicators
- Host statistics
- Pending operations (migrations, orphaned stacks)
- Quick actions via command palette

### Stack Detail (`/stack/{name}`)

- Compose file editor (Monaco)
- Environment file editor
- Action buttons: Up, Down, Restart, Update, Pull, Logs
- Container shell access (exec into running containers)
- Terminal output for running commands

### Console (`/console`)

- Full shell access to any host
- File editor for remote files
- Monaco editor with syntax highlighting

<video autoplay loop muted playsinline>
  <source src="/assets/web-console.webm" type="video/webm">
</video>

### Container Shell

Click the Shell button on any running container to exec into it directly from the browser.

<video autoplay loop muted playsinline>
  <source src="/assets/web-shell.webm" type="video/webm">
</video>

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+K` / `Cmd+K` | Open command palette |
| `Ctrl+S` / `Cmd+S` | Save editors |
| `Escape` | Close command palette |
| `Arrow keys` | Navigate command list |
| `Enter` | Execute selected command |

## Starting the Server

```bash
# Default: http://0.0.0.0:8000
cf web

# Custom port
cf web --port 3000

# Development mode with auto-reload
cf web --reload

# Bind to specific interface
cf web --host 127.0.0.1
```

## Requirements

The web UI requires additional dependencies:

```bash
# If installed via pip
pip install compose-farm[web]

# If installed via uv
uv tool install 'compose-farm[web]'
```

## Architecture

The web UI uses:

- **FastAPI** - Backend API and WebSocket handling
- **HTMX** - Dynamic page updates without full reloads
- **DaisyUI + Tailwind** - Theming and styling
- **Monaco Editor** - Code editing for compose/env files
- **xterm.js** - Terminal emulation for logs and shell access
