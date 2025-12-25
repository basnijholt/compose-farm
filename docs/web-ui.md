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
- Host statistics (CPU, memory, disk, load via Glances)
- Pending operations (migrations, orphaned stacks)
- Quick actions via command palette

### Live Stats (`/live-stats`)

Real-time container monitoring across all hosts, powered by [Glances](https://nicolargo.github.io/glances/).

- **Live metrics**: CPU, memory, network I/O for every container
- **Auto-refresh**: Updates every 3 seconds (pauses when dropdown menus are open)
- **Filtering**: Type to filter containers by name, stack, host, or image
- **Sorting**: Click column headers to sort by any metric
- **Update detection**: Shows when container images have updates available

<video autoplay loop muted playsinline>
  <source src="/assets/web-live_stats.webm" type="video/webm">
</video>

#### Requirements

Live Stats requires Glances to be deployed on all hosts:

1. Add `glances_stack: glances` to your `compose-farm.yaml`
2. Deploy a Glances stack that runs on all hosts (see [example](https://github.com/basnijholt/compose-farm/tree/main/examples/glances))
3. Glances must expose its REST API on port 61208

### Stack Detail (`/stack/{name}`)

- Compose file editor (Monaco)
- Environment file editor
- Action buttons: Up, Down, Restart, Update, Pull, Logs
- Container shell access (exec into running containers)
- Terminal output for running commands

Files are automatically backed up before saving to `~/.config/compose-farm/backups/`.

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
