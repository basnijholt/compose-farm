---
icon: lucide/server
---

# Compose Farm

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

## What is Compose Farm?

Compose Farm lets you manage Docker Compose stacks across multiple machines from a single command line. Think [Dockge](https://dockge.kuma.pet/) but with a CLI and web interface, designed for multi-host deployments.

Define which stacks run where in one YAML file, then use `cf apply` to make reality match your configuration.
It also works great on a single host with one folder per stack; just map stacks to `localhost`.

## Quick Demo

**CLI:**
<video autoplay loop muted playsinline>
  <source src="/assets/quickstart.webm" type="video/webm">
</video>

**[Web UI](web-ui.md):**
<video autoplay loop muted playsinline>
  <source src="/assets/web-workflow.webm" type="video/webm">
</video>

## Why Compose Farm?

| Problem | Compose Farm Solution |
|---------|----------------------|
| 100+ containers on one machine | Distribute across multiple hosts |
| Kubernetes too complex | Just SSH + docker compose |
| Swarm in maintenance mode | Zero infrastructure changes |
| Manual SSH for each host | Single command for all |

**It's a convenience wrapper, not a new paradigm.** Your existing `docker-compose.yml` files work unchanged.

## Quick Start

### Single host

No SSH, shared storage, or Traefik file-provider required.

```yaml
# compose-farm.yaml
compose_dir: /opt/stacks

hosts:
  local: localhost

stacks:
  plex: local
  jellyfin: local
  traefik: local
```

```bash
cf apply  # Start/stop stacks to match config
```

### Multi-host

Requires SSH plus a shared `compose_dir` path on all hosts (NFS or sync).

```yaml
# compose-farm.yaml
compose_dir: /opt/compose

hosts:
  server-1:
    address: 192.168.1.10
  server-2:
    address: 192.168.1.11

stacks:
  plex: server-1
  jellyfin: server-2
  grafana: server-1
```

```bash
cf apply  # Stacks start, migrate, or stop as needed
```

Each entry in `stacks:` maps to a folder under `compose_dir` that contains a compose file.

For cross-host HTTP routing, add Traefik labels and configure `traefik_file` to generate file-provider config.
### Installation

```bash
uv tool install compose-farm
# or
pip install compose-farm
```

### Configuration

Create `compose-farm.yaml` in the directory where you'll run commands (e.g., `/opt/stacks`), or in `~/.config/compose-farm/`:

```yaml
compose_dir: /opt/compose

hosts:
  nuc:
    address: 192.168.1.10
    user: docker
  hp:
    address: 192.168.1.11

stacks:
  plex: nuc
  grafana: nuc
  nextcloud: hp
```

See [Configuration](configuration.md) for all options and the full search order.

### Usage

```bash
# Make reality match config
cf apply

# Start specific stacks
cf up plex grafana

# Check status
cf ps

# View logs
cf logs -f plex
```

## Key Features

- **Declarative configuration**: One YAML defines where everything runs
- **Auto-migration**: Change a host assignment, run `cf up`, stack moves automatically

<video autoplay loop muted playsinline>
  <source src="/assets/migration.webm" type="video/webm">
</video>
- **Parallel execution**: Multiple stacks start/stop concurrently
- **State tracking**: Knows which stacks are running where
- **Traefik integration**: Generate file-provider config for cross-host routing
- **Zero changes**: Your compose files work as-is

## Requirements

- [uv](https://docs.astral.sh/uv/) (recommended) or Python 3.11+
- SSH key-based authentication to your Docker hosts
- Docker and Docker Compose on all target hosts
- Shared storage (compose files at same path on all hosts)

## Documentation

- [Getting Started](getting-started.md) - Installation and first steps
- [Configuration](configuration.md) - All configuration options
- [Commands](commands.md) - CLI reference
- [Web UI](web-ui.md) - Browser-based management interface
- [Architecture](architecture.md) - How it works under the hood
- [Traefik Integration](traefik.md) - Multi-host routing setup
- [Best Practices](best-practices.md) - Tips and limitations

## License

MIT
