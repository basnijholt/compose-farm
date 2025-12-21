---
icon: lucide/layers
---

# Architecture

This document explains how Compose Farm works under the hood.

## Design Philosophy

Compose Farm follows three core principles:

1. **KISS** - Keep it simple. It's a thin wrapper around `docker compose` over SSH.
2. **YAGNI** - No orchestration, no service discovery, no health checks until needed.
3. **Zero changes** - Your existing compose files work unchanged.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Compose Farm CLI                         │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │  Config  │  │  State   │  │Operations│  │   Executor       │ │
│  │  Parser  │  │ Tracker  │  │  Logic   │  │  (SSH/Local)     │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘ │
└───────┼─────────────┼─────────────┼─────────────────┼───────────┘
        │             │             │                 │
        ▼             ▼             ▼                 ▼
┌───────────────────────────────────────────────────────────────┐
│                         SSH / Local                            │
└───────────────────────────────────────────────────────────────┘
        │                                             │
        ▼                                             ▼
┌───────────────┐                           ┌───────────────┐
│   Host: nuc   │                           │   Host: hp    │
│               │                           │               │
│ docker compose│                           │ docker compose│
│    up -d      │                           │    up -d      │
└───────────────┘                           └───────────────┘
```

## Core Components

### Configuration (`src/compose_farm/config.py`)

Pydantic models for YAML configuration:

- **Config** - Root configuration with compose_dir, hosts, stacks
- **Host** - Host address, SSH user, and port

Key features:
- Validation with Pydantic
- Multi-host stack expansion (`all` → list of hosts)
- YAML loading with sensible defaults

### State Tracking (`src/compose_farm/state.py`)

Tracks deployment state in `compose-farm-state.yaml` (stored alongside the config file):

```yaml
deployed:
  plex: nuc
  grafana: nuc
```

Used for:
- Detecting migrations (stack moved to different host)
- Identifying orphans (stacks removed from config)
- `cf ps` status display

### Operations (`src/compose_farm/operations.py`)

Business logic for stack operations:

- **up** - Start stack, handle migration if needed
- **down** - Stop stack
- **preflight checks** - Verify mounts, networks exist before operations
- **discover** - Find running stacks on hosts
- **migrate** - Down on old host, up on new host

### Executor (`src/compose_farm/executor.py`)

SSH and local command execution:

- **Hybrid SSH approach**: asyncssh for parallel streaming, native `ssh -t` for raw mode
- **Parallel by default**: Multiple stacks via `asyncio.gather`
- **Streaming output**: Real-time stdout/stderr with `[stack]` prefix
- **Local detection**: Skips SSH when target matches local machine IP

### CLI (`src/compose_farm/cli/`)

Typer-based CLI with subcommand modules:

```
cli/
├── app.py          # Shared Typer app, version callback
├── common.py       # Shared helpers, options, progress utilities
├── config.py       # config subcommand (init, show, path, validate, edit, symlink)
├── lifecycle.py    # up, down, stop, pull, restart, update, apply, compose
├── management.py   # refresh, check, init-network, traefik-file
├── monitoring.py   # logs, ps, stats
├── ssh.py          # SSH key management (setup, status, keygen)
└── web.py          # Web UI server command
```

## Command Flow

### cf up plex

```
1. Load configuration
   └─► Parse compose-farm.yaml
   └─► Validate stack exists

2. Check state
   └─► Load state.yaml
   └─► Is plex already running?
   └─► Is it on a different host? (migration needed)

3. Pre-flight checks
   └─► SSH to target host
   └─► Check compose file exists
   └─► Check required mounts exist
   └─► Check required networks exist

4. Execute migration (if needed)
   └─► SSH to old host
   └─► Run: docker compose down

5. Start stack
   └─► SSH to target host
   └─► cd /opt/compose/plex
   └─► Run: docker compose up -d

6. Update state
   └─► Write new state to state.yaml

7. Generate Traefik config (if configured)
   └─► Regenerate traefik file-provider
```

### cf apply

```
1. Load configuration and state

2. Compute diff
   ├─► Orphans: in state, not in config
   ├─► Migrations: in both, different host
   └─► Missing: in config, not in state

3. Stop orphans
   └─► For each orphan: cf down

4. Migrate stacks
   └─► For each migration: down old, up new

5. Start missing
   └─► For each missing: cf up

6. Update state
```

## SSH Execution

### Parallel Streaming (asyncssh)

For most operations, Compose Farm uses asyncssh:

```python
async def run_command(host, command):
    async with asyncssh.connect(host) as conn:
        result = await conn.run(command)
        return result.stdout, result.stderr
```

Multiple stacks run concurrently via `asyncio.gather`.

### Raw Mode (native ssh)

For commands needing PTY (progress bars, interactive):

```bash
ssh -t user@host "docker compose pull"
```

### Local Detection

When target host IP matches local machine:

```python
if is_local(host_address):
    # Run locally, no SSH
    subprocess.run(command)
else:
    # SSH to remote
    ssh.run(command)
```

## State Management

### State File

Location: `compose-farm-state.yaml` (stored alongside the config file)

```yaml
deployed:
  plex: nuc
  grafana: nuc
```

Image digests are stored separately in `dockerfarm-log.toml` (also in the config directory).

### State Transitions

```
Config Change          State Change           Action
─────────────────────────────────────────────────────
Add stack            Missing                 cf up
Remove stack         Orphaned                cf down
Change host           Migration               down old, up new
No change             No change               none (or refresh)
```

### cf refresh

Syncs state with reality by querying Docker on each host:

```bash
docker ps --format '{{.Names}}'
```

Updates state.yaml to match what's actually running.

## Compose File Discovery

For each stack, Compose Farm looks for compose files in:

```
{compose_dir}/{stack}/
├── compose.yaml         # preferred
├── compose.yml
├── docker-compose.yml
└── docker-compose.yaml
```

First match wins.

## Traefik Integration

### Label Extraction

Compose Farm parses Traefik labels from compose files:

```yaml
stacks:
  plex:
    labels:
      - traefik.enable=true
      - traefik.http.routers.plex.rule=Host(`plex.example.com`)
      - traefik.http.services.plex.loadbalancer.server.port=32400
```

### File Provider Generation

Converts labels to Traefik file-provider YAML:

```yaml
http:
  routers:
    plex:
      rule: Host(`plex.example.com`)
      service: plex
  services:
    plex:
      loadBalancer:
        servers:
          - url: http://192.168.1.10:32400
```

### Variable Resolution

Supports `${VAR}` and `${VAR:-default}` from:
1. Service's `.env` file
2. Current environment

## Error Handling

### Pre-flight Failures

Before any operation, Compose Farm checks:
- SSH connectivity
- Compose file existence
- Required mounts
- Required networks

If checks fail, operation aborts with clear error.

### Partial Failures

When operating on multiple stacks:
- Each stack is independent
- Failures are logged, but other stacks continue
- Exit code reflects overall success/failure

## Performance Considerations

### Parallel Execution

Services are started/stopped in parallel:

```python
await asyncio.gather(*[
    up_stack(stack) for stack in stacks
])
```

### SSH Multiplexing

For repeated connections to the same host, SSH reuses connections.

### Caching

- Config is parsed once per command
- State is loaded once, written once
- Host discovery results are cached during command

## Web UI Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Web UI                               │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   FastAPI   │  │    Jinja    │  │       HTMX          │  │
│  │   Backend   │  │  Templates  │  │   Dynamic Updates   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                                                             │
│  Pattern: Custom events, not hx-swap-oob                    │
│  Elements trigger on: cf:refresh from:body                  │
└─────────────────────────────────────────────────────────────┘
```

Icons use [Lucide](https://lucide.dev/). Add new icons as macros in `web/templates/partials/icons.html`.
