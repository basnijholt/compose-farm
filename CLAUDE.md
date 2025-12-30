# Compose Farm Development Guidelines

## Core Principles

- **KISS**: Keep it simple. This is a thin wrapper around `docker compose` over SSH.
- **YAGNI**: Don't add features until they're needed. No orchestration, no service discovery, no health checks.
- **DRY**: Reuse patterns. Common CLI options are defined once, SSH logic is centralized.

## Architecture

```
src/compose_farm/
├── cli/               # CLI subpackage
│   ├── __init__.py    # Imports modules to trigger command registration
│   ├── app.py         # Shared Typer app instance, version callback
│   ├── common.py      # Shared helpers, options, progress bar utilities
│   ├── config.py      # Config subcommand (init, show, path, validate, edit, symlink)
│   ├── lifecycle.py   # up, down, stop, pull, restart, update, apply, compose commands
│   ├── management.py  # refresh, check, init-network, traefik-file commands
│   ├── monitoring.py  # logs, ps, stats commands
│   ├── ssh.py         # SSH key management (setup, status, keygen)
│   └── web.py         # Web UI server command
├── config.py          # Pydantic models, YAML loading
├── compose.py         # Compose file parsing (.env, ports, volumes, networks)
├── console.py         # Shared Rich console instances
├── executor.py        # SSH/local command execution, streaming output
├── operations.py      # Business logic (up, migrate, discover, preflight checks)
├── state.py           # Deployment state tracking (which stack on which host)
├── logs.py            # Image digest snapshots (dockerfarm-log.toml)
├── paths.py           # Path utilities, config file discovery
├── ssh_keys.py        # SSH key path constants and utilities
├── traefik.py         # Traefik file-provider config generation from labels
└── web/               # Web UI (FastAPI + HTMX)
```

## Web UI Icons

Icons use [Lucide](https://lucide.dev/). Add new icons as macros in `web/templates/partials/icons.html` by copying SVG paths from their site. The `action_btn`, `stat_card`, and `collapse` macros in `components.html` accept an optional `icon` parameter.

## HTMX Patterns

- **Multi-element refresh**: Use custom events, not `hx-swap-oob`. Elements have `hx-trigger="cf:refresh from:body"` and JS calls `document.body.dispatchEvent(new CustomEvent('cf:refresh'))`. Simpler to debug/test.
- **SPA navigation**: Sidebar uses `hx-boost="true"` to AJAX-ify links.
- **Attribute inheritance**: Set `hx-target`/`hx-swap` on parent elements.

## Key Design Decisions

1. **Hybrid SSH approach**: asyncssh for parallel streaming with prefixes; native `ssh -t` for raw mode (progress bars)
2. **Parallel by default**: Multiple stacks run concurrently via `asyncio.gather`
3. **Streaming output**: Real-time stdout/stderr with `[stack]` prefix using Rich
4. **SSH key auth only**: Uses ssh-agent, no password handling (YAGNI)
5. **NFS assumption**: Compose files at same path on all hosts
6. **Local IP auto-detection**: Skips SSH when target host matches local machine's IP
7. **State tracking**: Tracks where stacks are deployed for auto-migration
8. **Pre-flight checks**: Verifies NFS mounts and Docker networks exist before starting/migrating

## Code Style

- **Imports at top level**: Never add imports inside functions unless they are explicitly marked with `# noqa: PLC0415` and a comment explaining it speeds up CLI startup. Heavy modules like `pydantic`, `yaml`, and `rich.table` are lazily imported to keep `cf --help` fast.

## Development Commands

Use `just` for common tasks. Run `just` to list available commands:

| Command | Description |
|---------|-------------|
| `just install` | Install dev dependencies |
| `just test` | Run all tests |
| `just test-cli` | Run CLI tests (parallel) |
| `just test-web` | Run web UI tests (parallel) |
| `just lint` | Lint, format, and type check |
| `just web` | Start web UI (port 9001) |
| `just doc` | Build and serve docs (port 9002) |
| `just clean` | Clean build artifacts |

## Testing

Run tests with `just test` or `uv run pytest`. Browser tests require Chromium (system-installed or via `playwright install chromium`):

```bash
# Unit tests only (parallel)
uv run pytest -m "not browser" -n auto

# Browser tests only (parallel)
uv run pytest -m browser -n auto

# All tests
uv run pytest
```

Browser tests are marked with `@pytest.mark.browser`. They use Playwright to test HTMX behavior, JavaScript functionality (sidebar filter, command palette, terminals), and content stability during navigation.

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
| `up`    | Start stacks (`docker compose up -d`), auto-migrates if host changed |
| `down`  | Stop stacks (`docker compose down`). Use `--orphaned` to stop stacks removed from config |
| `stop`  | Stop services without removing containers (`docker compose stop`) |
| `pull`  | Pull latest images |
| `restart` | `down` + `up -d` |
| `update` | `pull` + `build` + `down` + `up -d` |
| `apply` | Make reality match config: migrate stacks + stop orphans. Use `--dry-run` to preview |
| `compose` | Run any docker compose command on a stack (passthrough) |
| `logs`  | Show stack logs |
| `ps`    | Show status of all stacks |
| `stats` | Show overview (hosts, stacks, pending migrations; `--live` for container counts) |
| `refresh` | Update state from reality: discover running stacks, capture image digests |
| `check` | Validate config, traefik labels, mounts, networks; show host compatibility |
| `init-network` | Create Docker network on hosts with consistent subnet/gateway |
| `traefik-file` | Generate Traefik file-provider config from compose labels |
| `config` | Manage config files (init, init-env, show, path, validate, edit, symlink) |
| `ssh`   | Manage SSH keys (setup, status, keygen) |
| `web`   | Start web UI server |
