# Compose Farm Development Guidelines

## Core Principles

- **KISS**: Keep it simple. This is a thin wrapper around `docker compose` over SSH.
- **YAGNI**: Don't add features until they're needed. No orchestration, no service discovery, no health checks.
- **DRY**: Reuse patterns. Common CLI options are defined once, SSH logic is centralized.

## Architecture

```
compose_farm/
├── config.py  # Pydantic models, YAML loading
├── ssh.py     # asyncssh execution, streaming
└── cli.py     # Typer commands
```

## Key Design Decisions

1. **asyncssh over Paramiko/Fabric**: Native async support, built-in streaming
2. **Parallel by default**: Multiple services run concurrently via `asyncio.gather`
3. **Streaming output**: Real-time stdout/stderr with `[service]` prefix
4. **SSH key auth only**: Uses ssh-agent, no password handling (YAGNI)
5. **NFS assumption**: Compose files at same path on all hosts
6. **Local execution**: When host is `localhost`/`local`, skip SSH and run locally

## Development Notes

The user frequently dictates requirements, so watch for:
- Homophones (e.g., "right" vs "write", "their" vs "there")
- Similar-sounding words that may need clarification

## Commands Quick Reference

| Command | Docker Compose Equivalent |
|---------|--------------------------|
| `up`    | `docker compose up -d`   |
| `down`  | `docker compose down`    |
| `pull`  | `docker compose pull`    |
| `restart` | `down` + `up -d`       |
| `update` | `pull` + `down` + `up -d` |
| `logs`  | `docker compose logs`    |
| `ps`    | `docker compose ps`      |
