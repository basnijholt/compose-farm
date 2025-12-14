# Compose Farm Development Guidelines

## Core Principles

- **KISS**: Keep it simple. This is a thin wrapper around `docker compose` over SSH.
- **YAGNI**: Don't add features until they're needed. No orchestration, no service discovery, no health checks.
- **DRY**: Reuse patterns. Common CLI options are defined once, SSH logic is centralized.

## Architecture

```
compose_farm/
├── cli.py      # Typer commands (cf/compose-farm CLI)
├── config.py   # Pydantic models, YAML loading
├── ssh.py      # asyncssh execution, streaming, local detection
├── state.py    # Deployment state tracking (which service on which host)
├── logs.py     # Image digest snapshots (dockerfarm-log.toml)
└── traefik.py  # Traefik file-provider config generation from labels
```

## Key Design Decisions

1. **asyncssh over Paramiko/Fabric**: Native async support, built-in streaming
2. **Parallel by default**: Multiple services run concurrently via `asyncio.gather`
3. **Streaming output**: Real-time stdout/stderr with `[service]` prefix using Rich
4. **SSH key auth only**: Uses ssh-agent, no password handling (YAGNI)
5. **NFS assumption**: Compose files at same path on all hosts
6. **Local IP auto-detection**: Skips SSH when target host matches local machine's IP
7. **State tracking**: Tracks where services are deployed for auto-migration

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
| `down`  | Stop services (`docker compose down`) |
| `pull`  | Pull latest images |
| `restart` | `down` + `up -d` |
| `update` | `pull` + `down` + `up -d` |
| `logs`  | Show service logs |
| `ps`    | Show status of all services |
| `sync`  | Discover running services, update state, capture image digests |
| `check` | Validate config vs disk, check traefik labels have ports |
| `check-mounts` | Verify volume mount paths exist on target hosts |
| `traefik-file` | Generate Traefik file-provider config from compose labels |
