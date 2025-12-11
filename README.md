# Compose Farm

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

## Why Compose Farm?

I run 100+ Docker Compose stacks on an LXC container that frequently runs out of memory. I needed a way to distribute services across multiple machines without the complexity of:

- **Kubernetes**: Overkill for my use case. I don't need pods, services, ingress controllers, or YAML manifests 10x the size of my compose files.
- **Docker Swarm**: Effectively in maintenance mode—no longer being invested in by Docker.

**Compose Farm is intentionally simple**: one YAML config mapping services to hosts, and a CLI that runs `docker compose` commands over SSH. That's it.

## Key Assumption: Shared Storage

Compose Farm assumes **all your compose files are accessible at the same path on all hosts**. This is typically achieved via:

- **NFS mount** (e.g., `/opt/compose` mounted from a NAS)
- **Synced folders** (e.g., Syncthing, rsync)
- **Shared filesystem** (e.g., GlusterFS, Ceph)

```
# Example: NFS mount on all hosts
nas:/volume1/compose  →  /opt/compose (on nas01)
nas:/volume1/compose  →  /opt/compose (on nas02)
nas:/volume1/compose  →  /opt/compose (on nas03)
```

Compose Farm simply runs `docker compose -f /opt/compose/{service}/docker-compose.yml` on the appropriate host—it doesn't copy or sync files.

## Installation

```bash
pip install compose-farm
# or
uv pip install compose-farm
```

## Configuration

Create `~/.config/compose-farm/compose-farm.yaml` (or `./compose-farm.yaml` in your working directory):

```yaml
compose_dir: /opt/compose  # Must be the same path on all hosts

hosts:
  nas01:
    address: 192.168.1.10
    user: docker
  nas02:
    address: 192.168.1.11
    # user defaults to current user
  local: localhost  # Run locally without SSH

services:
  plex: nas01
  jellyfin: nas02
  sonarr: nas01
  radarr: local  # Runs on the machine where you invoke compose-farm
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

# Capture image digests to a TOML log (per service or all)
compose-farm snapshot plex
compose-farm snapshot --all  # writes ~/.config/compose-farm/dockerfarm-log.toml

# View logs
compose-farm logs plex
compose-farm logs -f plex  # follow

# Show status
compose-farm ps
```

## Requirements

- Python 3.11+
- SSH key-based authentication to your hosts (uses ssh-agent)
- Docker and Docker Compose installed on all target hosts
- **Shared storage**: All compose files at the same path on all hosts (NFS, Syncthing, etc.)

## How It Works

1. You run `compose-farm up plex`
2. Compose Farm looks up which host runs `plex` (e.g., `nas01`)
3. It SSHs to `nas01` (or runs locally if `localhost`)
4. It executes `docker compose -f /opt/compose/plex/docker-compose.yml up -d`
5. Output is streamed back with `[plex]` prefix

That's it. No orchestration, no service discovery, no magic.

## License

MIT
