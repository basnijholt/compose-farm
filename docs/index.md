---
icon: lucide/server
---

# Compose Farm

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

## What is Compose Farm?

Compose Farm lets you manage Docker Compose services across multiple machines from a single command line. Think [Dockge](https://dockge.kuma.pet/) but with a CLI and [web interface](web-ui.md), designed for multi-host deployments.

Define which services run where in one YAML file, then use `cf apply` to make reality match your configuration.

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

```yaml
# compose-farm.yaml
compose_dir: /opt/compose

hosts:
  server-1:
    address: 192.168.1.10
  server-2:
    address: 192.168.1.11

services:
  plex: server-1
  jellyfin: server-2
  sonarr: server-1
```

```bash
cf apply  # Services start, migrate, or stop as needed
```

### Installation

```bash
uv tool install compose-farm
# or
pip install compose-farm
```

### Configuration

Create `~/.config/compose-farm/compose-farm.yaml`:

```yaml
compose_dir: /opt/compose

hosts:
  nuc:
    address: 192.168.1.10
    user: docker
  hp:
    address: 192.168.1.11

services:
  plex: nuc
  sonarr: nuc
  radarr: hp
```

### Usage

```bash
# Make reality match config
cf apply

# Start specific services
cf up plex sonarr

# Check status
cf ps

# View logs
cf logs -f plex
```

## Key Features

- **Declarative configuration**: One YAML defines where everything runs
- **Auto-migration**: Change a host assignment, run `cf up`, service moves automatically

<video autoplay loop muted playsinline>
  <source src="/assets/migration.webm" type="video/webm">
</video>
- **Parallel execution**: Multiple services start/stop concurrently
- **State tracking**: Knows which services are running where
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
