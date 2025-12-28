---
icon: lucide/container
---

# Docker Deployment

Run Compose Farm in Docker for persistent web UI and CLI access without installing locally.

## Quick Start

```bash
# Clone the example docker-compose.yml
curl -O https://raw.githubusercontent.com/basnijholt/compose-farm/main/docker-compose.yml

# Create .env with your settings
cat >> .env << 'EOF'
DOMAIN=example.com
CF_UID=1000
CF_GID=1000
CF_HOME=/home/youruser
CF_USER=youruser
CF_LOCAL_HOST=nas
EOF

# Start the web UI
docker compose up -d web
```

## Services

The `docker-compose.yml` provides two services:

| Service | Purpose | Use Case |
|---------|---------|----------|
| `cf` | One-shot CLI commands | Run `docker compose run --rm cf <command>` |
| `web` | Persistent web UI server | Always-on dashboard at port 9000 |

## Environment Variables

### Required for Non-Root Operation

When mounting NFS volumes or preserving file ownership, run as your user instead of root:

| Variable | Description | Example |
|----------|-------------|---------|
| `CF_UID` | User ID to run as | `1000` |
| `CF_GID` | Group ID to run as | `1000` |
| `CF_HOME` | Home directory in container | `/home/youruser` |
| `CF_USER` | Username (required for SSH) | `youruser` |

Set these in `.env`:

```bash
echo "CF_UID=$(id -u)" >> .env
echo "CF_GID=$(id -g)" >> .env
echo "CF_HOME=$HOME" >> .env
echo "CF_USER=$USER" >> .env
```

### Paths and Config

| Variable | Description | Default |
|----------|-------------|---------|
| `CF_COMPOSE_DIR` | Directory containing compose files | `/opt/stacks` |
| `CF_CONFIG` | Path to compose-farm.yaml | `$CF_COMPOSE_DIR/compose-farm.yaml` |
| `CF_SSH_DIR` | SSH keys directory | `~/.ssh/compose-farm` |
| `CF_XDG_CONFIG` | Config/backup directory | `~/.config/compose-farm` |

### Web UI Specific

| Variable | Description | Default |
|----------|-------------|---------|
| `CF_WEB_STACK` | Stack name (for self-update detection) | `compose-farm` |
| `CF_LOCAL_HOST` | Local hostname for Glances | *(none)* |
| `DOMAIN` | Domain for Traefik labels | *(required)* |

## SSH Authentication

The container needs SSH access to remote hosts. Choose one option:

### Option 1: Dedicated Keys (Recommended)

Generate keys with `cf ssh setup` on the host, then mount them:

```yaml
volumes:
  - ~/.ssh/compose-farm:/root/.ssh/compose-farm
```

The container uses `~/.ssh/compose-farm/id_ed25519` automatically.

### Option 2: SSH Agent Forwarding

Forward your SSH agent socket:

```yaml
volumes:
  - ${SSH_AUTH_SOCK}:/ssh-agent:ro
environment:
  - SSH_AUTH_SOCK=/ssh-agent
```

!!! note
    Agent forwarding requires the socket path. Set `SSH_AUTH_SOCK` in your environment or `.env`.

## Glances Integration

When running the web UI in Docker, the local host's Glances may not be reachable via IP due to Docker network isolation. Set `CF_LOCAL_HOST` to your local hostname:

```bash
echo "CF_LOCAL_HOST=nas" >> .env
```

This tells the web UI to reach the local Glances via container name instead of IP (both containers are on the same Docker network).

### Why is this needed?

```
┌─────────────────────────────────────────────────────────┐
│  Docker Host (nas)                                      │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │ compose-farm-web │    │ glances          │          │
│  │ (172.20.0.x)     │    │ (172.20.0.y)     │          │
│  └────────┬─────────┘    └──────────────────┘          │
│           │                                             │
│           │ HTTP to 192.168.1.6:61208 ❌ BLOCKED        │
│           │ HTTP to glances:61208    ✅ WORKS           │
└─────────────────────────────────────────────────────────┘
```

The web container can't reach the host's LAN IP from the Docker bridge network, but can reach other containers by name on the same network.

## Complete Example

```yaml
services:
  web:
    image: ghcr.io/basnijholt/compose-farm:latest
    restart: unless-stopped
    command: web --host 0.0.0.0 --port 9000
    user: "${CF_UID:-0}:${CF_GID:-0}"
    volumes:
      # Compose files directory
      - ${CF_COMPOSE_DIR:-/opt/stacks}:${CF_COMPOSE_DIR:-/opt/stacks}
      # SSH keys for remote hosts
      - ${CF_SSH_DIR:-~/.ssh/compose-farm}:${CF_HOME:-/root}/.ssh/compose-farm
      # Backups and state
      - ${CF_XDG_CONFIG:-~/.config/compose-farm}:${CF_HOME:-/root}/.config/compose-farm
    environment:
      - CF_CONFIG=${CF_COMPOSE_DIR:-/opt/stacks}/compose-farm.yaml
      - CF_WEB_STACK=compose-farm
      - CF_LOCAL_HOST=${CF_LOCAL_HOST:-}
      - HOME=${CF_HOME:-/root}
      - USER=${CF_USER:-root}
    labels:
      - traefik.enable=true
      - traefik.http.routers.compose-farm.rule=Host(`compose-farm.${DOMAIN}`)
      - traefik.http.routers.compose-farm.entrypoints=websecure
      - traefik.http.services.compose-farm.loadbalancer.server.port=9000
    networks:
      - mynetwork

networks:
  mynetwork:
    external: true
```

## .env File Template

```bash
# Domain for Traefik routing
DOMAIN=lab.example.com

# Run as current user (required for NFS)
CF_UID=1000
CF_GID=1000
CF_HOME=/home/youruser
CF_USER=youruser

# Compose files location
CF_COMPOSE_DIR=/opt/stacks

# SSH keys location
CF_SSH_DIR=/home/youruser/.ssh/compose-farm

# Backups and state
CF_XDG_CONFIG=/home/youruser/.config/compose-farm

# Local hostname for Glances (required for Docker deployment)
CF_LOCAL_HOST=nas
```

## Troubleshooting

### SSH "Host key verification failed"

The container needs known_hosts. Create an SSH config in your compose-farm SSH directory:

```bash
cat > ~/.ssh/compose-farm/config << 'EOF'
Host *
    IdentityFile ~/.ssh/compose-farm/id_ed25519
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
EOF
chmod 600 ~/.ssh/compose-farm/config
```

### Glances shows "timeout" for local host

Set `CF_LOCAL_HOST` to your local hostname in `.env`:

```bash
echo "CF_LOCAL_HOST=nas" >> .env
docker compose up -d web  # Restart to apply
```

### Permission denied on NFS volumes

Run as your user instead of root. Add to `.env`:

```bash
CF_UID=$(id -u)
CF_GID=$(id -g)
CF_HOME=$HOME
CF_USER=$USER
```

### Web UI can't reach remote hosts

Check SSH connectivity from the container:

```bash
docker compose exec web cf ssh status
```

If hosts show "Auth failed", regenerate keys:

```bash
docker compose run --rm cf ssh setup
```
