# Compose Farm

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

## Why Compose Farm?

I run 100+ Docker Compose stacks on an LXC container that frequently runs out of memory. I needed a way to distribute services across multiple machines without the complexity of:

- **Kubernetes**: Overkill for my use case. I don't need pods, services, ingress controllers, or YAML manifests 10x the size of my compose files.
- **Docker Swarm**: Effectively in maintenance mode—no longer being invested in by Docker.

**Compose Farm is intentionally simple**: one YAML config mapping services to hosts, and a CLI that runs `docker compose` commands over SSH. That's it.

## Installation

```bash
pip install compose-farm
# or
uv pip install compose-farm
```

## Configuration

Create `~/.config/compose-farm/compose-farm.yaml` (or `./compose-farm.yaml` in your working directory):

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
compose-farm up plex jellyfin
compose-farm up --all

# Stop services
compose-farm down plex

# Pull latest images
compose-farm pull --all

# Restart (down + up)
compose-farm restart plex

# Update (pull + down + up) - the end-to-end update command
compose-farm update --all

# View logs
compose-farm logs plex
compose-farm logs -f plex  # follow

# Show status
compose-farm ps
```

## Requirements

- Python 3.11+
- SSH key-based authentication to your hosts (uses ssh-agent)
- Docker and Docker Compose on target hosts
- Compose files accessible via NFS or similar (same path on all hosts)

## License

MIT
