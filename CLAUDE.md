# Compose Farm Development Guidelines

## Core Principles

- **KISS**: Keep it simple. This is a thin wrapper around `docker compose` over SSH.
- **YAGNI**: Don't add features until they're needed. No orchestration, no service discovery, no health checks.
- **DRY**: Reuse patterns. Common CLI options are defined once, SSH logic is centralized.

## Architecture

```
compose_farm/
├── cli/               # CLI subpackage
│   ├── __init__.py    # Imports modules to trigger command registration
│   ├── app.py         # Shared Typer app instance, version callback
│   ├── common.py      # Shared helpers, options, progress bar utilities
│   ├── config.py      # Config subcommand (init, show, path, validate, edit)
│   ├── lifecycle.py   # up, down, pull, restart, update, apply commands
│   ├── management.py  # refresh, check, init-network, traefik-file commands
│   └── monitoring.py  # logs, ps, stats commands
├── config.py          # Pydantic models, YAML loading
├── compose.py         # Compose file parsing (.env, ports, volumes, networks)
├── console.py         # Shared Rich console instances
├── executor.py        # SSH/local command execution, streaming output
├── operations.py      # Business logic (up, migrate, discover, preflight checks)
├── state.py           # Deployment state tracking (which service on which host)
├── logs.py            # Image digest snapshots (dockerfarm-log.toml)
└── traefik.py         # Traefik file-provider config generation from labels
```

## Web UI Icons

Icons use [Lucide](https://lucide.dev/). Add new icons as macros in `web/templates/partials/icons.html` by copying SVG paths from their site. The `action_btn`, `stat_card`, and `collapse` macros in `components.html` accept an optional `icon` parameter.

## HTMX Patterns

The web UI uses [HTMX](https://htmx.org/) for dynamic behavior. Follow these patterns:

### Core Attributes

```html
<button hx-post="/api/action" hx-target="#result" hx-swap="innerHTML">
```

- `hx-get`, `hx-post`, `hx-put`, `hx-delete` - HTTP verbs
- `hx-target` - CSS selector for where response goes
- `hx-swap` - How to insert (`innerHTML`, `outerHTML`, `beforeend`, etc.)
- `hx-trigger` - Event that triggers request (default: `click` for buttons)

### Updating Multiple Elements (use custom events)

When an action needs to update multiple parts of the page, use **custom events** with `hx-trigger`. Elements listen for the event and refresh themselves independently:

```html
<!-- Elements that auto-refresh on event -->
<div id="stats-cards"
     hx-get="/partials/stats-content"
     hx-trigger="cf:refresh from:body"
     hx-swap="innerHTML">
</div>

<!-- JavaScript dispatches the event after an action -->
<script>
document.body.dispatchEvent(new CustomEvent('cf:refresh'));
</script>
```

This approach is preferred over `hx-swap-oob` (Out of Band Swaps) because:
- **Simpler**: No need to construct multi-part responses on the server
- **Easier to debug**: Each element makes an isolated request you can inspect
- **Easier to maintain**: Loose coupling between action and refreshed elements
- **Easier to test**: Endpoints can be tested independently

Reference: [Triggering Events](https://htmx.org/headers/hx-trigger/)

### SPA-like Navigation (`hx-boost`)

Use `hx-boost="true"` on containers to make all child links/forms use AJAX:

```html
<nav hx-boost="true" hx-target="#main-content" hx-select="#main-content" hx-swap="innerHTML">
    <a href="/page">Link</a>  <!-- Automatically AJAX-ified -->
</nav>
```

### Attribute Inheritance

HTMX attributes inherit to children. Set common attributes on parent elements:

```html
<div hx-target="#result" hx-swap="innerHTML">
    <button hx-post="/a">A</button>  <!-- Inherits target/swap -->
    <button hx-post="/b">B</button>
</div>
```

### Working with Monaco Editors

Since Monaco content isn't in form fields, use `hx-vals` with a JS function:

```html
<button hx-put="/api/config"
        hx-vals="js:{content: editors['config']?.getValue()}"
        hx-target="#save-result">
    Save
</button>
```

Or use `htmx.ajax()` from JavaScript when more control is needed:

```javascript
htmx.ajax('PUT', '/api/config', {
    values: { content: editor.getValue() },
    target: '#save-result',
    swap: 'innerHTML'
});
```

## Key Design Decisions

1. **Hybrid SSH approach**: asyncssh for parallel streaming with prefixes; native `ssh -t` for raw mode (progress bars)
2. **Parallel by default**: Multiple services run concurrently via `asyncio.gather`
3. **Streaming output**: Real-time stdout/stderr with `[service]` prefix using Rich
4. **SSH key auth only**: Uses ssh-agent, no password handling (YAGNI)
5. **NFS assumption**: Compose files at same path on all hosts
6. **Local IP auto-detection**: Skips SSH when target host matches local machine's IP
7. **State tracking**: Tracks where services are deployed for auto-migration
8. **Pre-flight checks**: Verifies NFS mounts and Docker networks exist before starting/migrating

## Code Style

- **Imports at top level**: Never add imports inside functions unless they are explicitly marked with `# noqa: PLC0415` and a comment explaining it speeds up CLI startup. Heavy modules like `pydantic`, `yaml`, and `rich.table` are lazily imported to keep `cf --help` fast.

## Communication Notes

- Clarify ambiguous wording (e.g., homophones like "right"/"write", "their"/"there").

## Git Safety

- Never amend commits.
- **NEVER merge anything into main.** Always commit directly or use fast-forward/rebase.
- Never force push.

## Pull Requests

- Never include unchecked checklists (e.g., `- [ ] ...`) in PR descriptions. Either omit the checklist or use checked items.
- **NEVER run `gh pr merge`**. PRs are merged via the GitHub UI, not the CLI.

## Releases

Use `gh release create` to create releases. The tag is created automatically.

```bash
# Check current version
git tag --sort=-v:refname | head -1

# Create release (minor version bump: v0.21.1 -> v0.22.0)
gh release create v0.22.0 --title "v0.22.0" --notes "release notes here"
```

Versioning:
- **Patch** (v0.21.0 → v0.21.1): Bug fixes
- **Minor** (v0.21.1 → v0.22.0): New features, non-breaking changes

Write release notes manually describing what changed. Group by features and bug fixes.

## Commands Quick Reference

CLI available as `cf` or `compose-farm`.

| Command | Description |
|---------|-------------|
| `up`    | Start services (`docker compose up -d`), auto-migrates if host changed |
| `down`  | Stop services (`docker compose down`). Use `--orphaned` to stop services removed from config |
| `pull`  | Pull latest images |
| `restart` | `down` + `up -d` |
| `update` | `pull` + `down` + `up -d` |
| `apply` | Make reality match config: migrate services + stop orphans. Use `--dry-run` to preview |
| `logs`  | Show service logs |
| `ps`    | Show status of all services |
| `stats` | Show overview (hosts, services, pending migrations; `--live` for container counts) |
| `refresh` | Update state from reality: discover running services, capture image digests |
| `check` | Validate config, traefik labels, mounts, networks; show host compatibility |
| `init-network` | Create Docker network on hosts with consistent subnet/gateway |
| `traefik-file` | Generate Traefik file-provider config from compose labels |
| `config` | Manage config files (init, show, path, validate, edit) |
