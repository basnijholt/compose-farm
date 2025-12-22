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
  <source src="/assets/install.webm" type="video/webm">
</video>

### One-liner (recommended)

```bash
curl -fsSL https://compose-farm.nijho.lt/install | sh
```

This installs [uv](https://docs.astral.sh/uv/) if needed, then installs compose-farm.

### Using uv

If you already have [uv](https://docs.astral.sh/uv/) installed:

```bash
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

**Running as non-root user** (recommended for NFS mounts):

By default, containers run as root. To preserve file ownership on mounted volumes, set these environment variables in your `.env` file:

```bash
# Add to .env file (one-time setup)
echo "CF_UID=$(id -u)" >> .env
echo "CF_GID=$(id -g)" >> .env
echo "CF_HOME=$HOME" >> .env
echo "CF_USER=$USER" >> .env
```

Or use [direnv](https://direnv.net/) to auto-set these variables when entering the directory:
```bash
cp .envrc.example .envrc && direnv allow
```

This ensures files like `compose-farm-state.yaml` and web UI edits are owned by your user instead of root. The `CF_USER` variable is required for SSH to work when running as a non-root user.

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
├── grafana/
│   └── docker-compose.yml
├── nextcloud/
│   └── docker-compose.yml
└── jellyfin/
    └── docker-compose.yml
```

## Configuration

### Create Config File

Create `compose-farm.yaml` in the directory where you'll run commands. For example, if your stacks are in `/opt/stacks`, place the config there too:

```bash
cd /opt/stacks
cf config init
```

Alternatively, use `~/.config/compose-farm/compose-farm.yaml` for a global config. You can also symlink a working directory config to the global location:

```bash
# Create config in your stacks directory, symlink to ~/.config
cf config symlink /opt/stacks/compose-farm.yaml
```

This way, `cf` commands work from anywhere while the config lives with your stacks.

#### Single host example

```yaml
# Where compose files are located (one folder per stack)
compose_dir: /opt/stacks

hosts:
  local: localhost

stacks:
  plex: local
  grafana: local
  nextcloud: local
```

#### Multi-host example
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

# Map stacks to hosts
stacks:
  plex: nuc
  grafana: nuc
  nextcloud: hp
```

Each entry in `stacks:` maps to a folder under `compose_dir` that contains a compose file.

For cross-host HTTP routing, add Traefik labels and configure `traefik_file` (see [Traefik Integration](traefik.md)).
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

Shows all configured stacks and their status.

### Start All Stacks

```bash
cf up --all
```

Starts all stacks on their assigned hosts.

### Start Specific Stacks

```bash
cf up plex grafana
```

### Apply Configuration

The most powerful command - reconciles reality with your config:

```bash
cf apply --dry-run   # Preview changes
cf apply             # Execute changes
```

This will:
1. Start stacks in config but not running
2. Migrate stacks on wrong host
3. Stop stacks removed from config

## Docker Network Setup

If your stacks use an external Docker network:

```bash
# Create network on all hosts
cf init-network

# Or specific hosts
cf init-network nuc hp
```

Default network: `mynetwork` with subnet `172.20.0.0/16`

## Example Workflow

### 1. Add a New Stack

Create the compose file:

```bash
# On any host (shared storage)
mkdir -p /opt/compose/gitea
cat > /opt/compose/gitea/docker-compose.yml << 'EOF'
services:
  gitea:
    image: docker.gitea.com/gitea:latest
    container_name: gitea
    environment:
      - USER_UID=1000
      - USER_GID=1000
    volumes:
      - /opt/config/gitea:/data
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "3000:3000"
      - "2222:22"
    restart: unless-stopped
EOF
```

Add to config:

```yaml
stacks:
  # ... existing stacks
  gitea: nuc
```

Start the stack:

```bash
cf up gitea
```

### 2. Move a Stack to Another Host

Edit `compose-farm.yaml`:

```yaml
stacks:
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

### 3. Update All Stacks

```bash
cf update --all
# Runs: pull + build + down + up for each stack
```

## Next Steps

- [Configuration Reference](configuration.md) - All config options
- [Commands Reference](commands.md) - Full CLI documentation
- [Traefik Integration](traefik.md) - Multi-host routing
- [Best Practices](best-practices.md) - Tips and limitations
