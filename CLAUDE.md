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

## Key Design Decisions

1. **Hybrid SSH approach**: asyncssh for parallel streaming with prefixes; native `ssh -t` for raw mode (progress bars)
2. **Parallel by default**: Multiple services run concurrently via `asyncio.gather`
3. **Streaming output**: Real-time stdout/stderr with `[service]` prefix using Rich
4. **SSH key auth only**: Uses ssh-agent, no password handling (YAGNI)
5. **NFS assumption**: Compose files at same path on all hosts
6. **Local IP auto-detection**: Skips SSH when target host matches local machine's IP
7. **State tracking**: Tracks where services are deployed for auto-migration
8. **Pre-flight checks**: Verifies NFS mounts and Docker networks exist before starting/migrating

## Communication Notes

- Clarify ambiguous wording (e.g., homophones like "right"/"write", "their"/"there").

## Git Safety

- Never amend commits.
- **NEVER merge anything into main.** Always commit directly or use fast-forward/rebase.
- Never force push.

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
