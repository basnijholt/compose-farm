# SDC (Simple Distributed Compose) - Implementation Plan

## Overview
Minimal CLI tool to run docker compose commands on remote hosts via SSH.

## Tech Stack
- **uv** - project init & dependency management
- **Hatch** - build backend
- **Typer** - CLI
- **Pydantic** - config parsing
- **asyncssh** - async SSH with streaming

## Project Structure
```
sdc/
├── pyproject.toml
├── src/
│   └── sdc/
│       ├── __init__.py
│       ├── cli.py          # Typer CLI
│       ├── config.py       # Pydantic models
│       └── ssh.py          # SSH execution
└── sdc.yaml                # Example config
```

## Config Schema (`~/.config/sdc/sdc.yaml` or `./sdc.yaml`)
```yaml
compose_dir: /opt/compose

hosts:
  nas01:
    address: 192.168.1.10
    user: docker        # optional, defaults to current user
  nas02:
    address: 192.168.1.11

services:
  plex: nas01
  jellyfin: nas02
```

## CLI Commands
```bash
sdc up <service...>       # docker compose up -d
sdc down <service...>     # docker compose down
sdc pull <service...>     # docker compose pull
sdc restart <service...>  # down + up
sdc logs <service...>     # stream logs
sdc ps                    # show all services & status
sdc update <service...>   # pull + restart (end-to-end update)

# Flags
--all                     # run on all services
```

## Implementation Steps

### Step 1: Project Setup
- `uv init sdc`
- Configure pyproject.toml with Hatch build backend
- Add dependencies: typer, pydantic, asyncssh, pyyaml

### Step 2: Config Module (`config.py`)
- Pydantic models: `Host`, `Config`
- Config loading: check `./sdc.yaml` then `~/.config/sdc/sdc.yaml`
- Validation: ensure services reference valid hosts

### Step 3: SSH Module (`ssh.py`)
- `run_command(host, command)` - async SSH with streaming
- `run_compose(service, compose_cmd)` - build and run compose command
- `run_on_services(services, compose_cmd)` - parallel execution

### Step 4: CLI Module (`cli.py`)
- Typer app with commands: up, down, pull, restart, logs, ps, update
- `--all` flag for operating on all services
- Streaming output with service name prefix

## Design Decisions
1. **asyncssh** - native async, built-in streaming stdout/stderr
2. **SSH key auth** - uses ssh-agent, no password handling
3. **Parallel by default** - asyncio.gather for multiple services
4. **Streaming output** - real-time with `[service]` prefix
5. **Compose path** - `{compose_dir}/{service}/docker-compose.yml`
