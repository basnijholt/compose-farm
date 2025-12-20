---
icon: lucide/rocket
---

# Getting Started

This guide walks you through installing Compose Farm and setting up your first multi-host deployment.

## Prerequisites

Before you begin, ensure you have:

- **[uv](https://docs.astral.sh/uv/)** (recommended) or Python 3.11+
- **SSH key-based authentication** to your Docker hosts
- **Docker and Docker Compose** installed on all target hosts
- **Shared storage** for compose files (NFS, Syncthing, etc.)

## Installation

<video autoplay loop muted playsinline>
  <source src="assets/install.webm" type="video/webm">
</video>

### Using uv (recommended)

[uv](https://docs.astral.sh/uv/) is the recommended way to install Compose Farm. It handles Python installation automatically.

```bash
# Install uv first (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install compose-farm
uv tool install compose-farm
```

### Using pip

If you already have Python 3.11+ installed:

```bash
pip install compose-farm
```

### Using Docker

```bash
docker run --rm \
  -v $SSH_AUTH_SOCK:/ssh-agent -e SSH_AUTH_SOCK=/ssh-agent \
  -v ./compose-farm.yaml:/root/.config/compose-farm/compose-farm.yaml:ro \
  ghcr.io/basnijholt/compose-farm up --all
```

### Verify Installation

```bash
cf --version
cf --help
```

## SSH Setup

Compose Farm uses SSH to run commands on remote hosts. You need passwordless SSH access.

### Option 1: SSH Agent (default)

If you already have SSH keys loaded in your agent:

```bash
# Verify keys are loaded
ssh-add -l

# Test connection
ssh user@192.168.1.10 "docker --version"
```

### Option 2: Dedicated Key (recommended for Docker)

For persistent access when running in Docker:

```bash
# Generate and distribute key to all hosts
cf ssh setup

# Check status
cf ssh status
```

This creates `~/.ssh/compose-farm/id_ed25519` and copies the public key to each host.

## Shared Storage Setup

Compose files must be accessible at the **same path** on all hosts. Common approaches:

### NFS Mount

```bash
# On each Docker host
sudo mount nas:/volume1/compose /opt/compose

# Or add to /etc/fstab
nas:/volume1/compose /opt/compose nfs defaults 0 0
```

### Directory Structure

```
/opt/compose/           # compose_dir in config
├── plex/
│   └── docker-compose.yml
├── sonarr/
│   └── docker-compose.yml
├── radarr/
│   └── docker-compose.yml
└── jellyfin/
    └── docker-compose.yml
```

## Configuration

### Create Config File

Create `~/.config/compose-farm/compose-farm.yaml`:

```yaml
# Where compose files are located (same path on all hosts)
compose_dir: /opt/compose

# Define your Docker hosts
hosts:
  nuc:
    address: 192.168.1.10
    user: docker           # SSH user
  hp:
    address: 192.168.1.11
    # user defaults to current user
  local: localhost         # Run locally without SSH

# Map services to hosts
services:
  plex: nuc
  sonarr: nuc
  radarr: hp
  jellyfin: local
```

### Validate Configuration

```bash
cf check --local
```

This validates syntax without SSH connections. For full validation:

```bash
cf check
```

## First Commands

### Check Status

```bash
cf ps
```

Shows all configured services and their status.

### Start All Services

```bash
cf up --all
```

Starts all services on their assigned hosts.

### Start Specific Services

```bash
cf up plex sonarr
```

### Apply Configuration

The most powerful command - reconciles reality with your config:

```bash
cf apply --dry-run   # Preview changes
cf apply             # Execute changes
```

This will:
1. Start services in config but not running
2. Migrate services on wrong host
3. Stop services removed from config

## Docker Network Setup

If your services use an external Docker network:

```bash
# Create network on all hosts
cf init-network

# Or specific hosts
cf init-network nuc hp
```

Default network: `mynetwork` with subnet `172.20.0.0/16`

## Example Workflow

### 1. Add a New Service

Create the compose file:

```bash
# On any host (shared storage)
mkdir -p /opt/compose/prowlarr
cat > /opt/compose/prowlarr/docker-compose.yml << 'EOF'
services:
  prowlarr:
    image: lscr.io/linuxserver/prowlarr:latest
    container_name: prowlarr
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - /opt/config/prowlarr:/config
    ports:
      - "9696:9696"
    restart: unless-stopped
EOF
```

Add to config:

```yaml
services:
  # ... existing services
  prowlarr: nuc
```

Start the service:

```bash
cf up prowlarr
```

### 2. Move a Service to Another Host

Edit `compose-farm.yaml`:

```yaml
services:
  plex: hp  # Changed from nuc
```

Apply the change:

```bash
cf up plex
# Automatically: down on nuc, up on hp
```

Or use apply to reconcile everything:

```bash
cf apply
```

### 3. Update All Services

```bash
cf update --all
# Runs: pull + down + up for each service
```

## Next Steps

- [Configuration Reference](configuration.md) - All config options
- [Commands Reference](commands.md) - Full CLI documentation
- [Traefik Integration](traefik.md) - Multi-host routing
- [Best Practices](best-practices.md) - Tips and limitations
