# SDC - Simple Distributed Compose

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

## Why SDC?

I run 100+ Docker Compose stacks on an LXC container that frequently runs out of memory. I needed a way to distribute services across multiple machines without the complexity of:

- **Kubernetes**: Overkill for my use case. I don't need pods, services, ingress controllers, or YAML manifests 10x the size of my compose files.
- **Docker Swarm**: Effectively in maintenance modeno longer being invested in by Docker.

**SDC is intentionally simple**: one YAML config mapping services to hosts, and a CLI that runs `docker compose` commands over SSH. That's it.

## Installation

```bash
pip install sdc
# or
uv pip install sdc
```

## Configuration

Create `~/.config/sdc/sdc.yaml` (or `./sdc.yaml` in your working directory):

```yaml
compose_dir: /opt/compose

hosts:
  nas01:
    address: 192.168.1.10
    user: docker
  nas02:
    address: 192.168.1.11
    # user defaults to current user

services:
  plex: nas01
  jellyfin: nas02
  sonarr: nas01
  radarr: nas02
```

Compose files are expected at `{compose_dir}/{service}/docker-compose.yml`.

## Usage

```bash
# Start services
sdc up plex jellyfin
sdc up --all

# Stop services
sdc down plex

# Pull latest images
sdc pull --all

# Restart (down + up)
sdc restart plex

# Update (pull + down + up) - the end-to-end update command
sdc update --all

# View logs
sdc logs plex
sdc logs -f plex  # follow

# Show status
sdc ps
```

## Requirements

- Python 3.11+
- SSH key-based authentication to your hosts (uses ssh-agent)
- Docker and Docker Compose on target hosts
- Compose files accessible via NFS or similar (same path on all hosts)

## License

MIT
