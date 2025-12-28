---
icon: lucide/container
---

# Docker Deployment

Run the Compose Farm web UI in Docker.

## Quick Start

**1. Get the compose file:**

```bash
curl -O https://raw.githubusercontent.com/basnijholt/compose-farm/main/docker-compose.yml
```

**2. Create `.env` file:**

```bash
cat > .env << 'EOF'
DOMAIN=example.com
CF_COMPOSE_DIR=/opt/stacks
EOF
```

**3. Set up SSH keys:**

```bash
# Generate keys and copy to your hosts
docker compose run --rm cf ssh setup
```

**4. Start the web UI:**

```bash
docker compose up -d web
```

Open `http://localhost:9000` (or `https://compose-farm.example.com` if using Traefik).

---

## Configuration

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DOMAIN` | Your domain (for Traefik labels) | `lab.example.com` |
| `CF_COMPOSE_DIR` | Path to your compose files | `/opt/stacks` |

### Optional: Run as Non-Root

Required if using NFS mounts or you want files owned by your user:

```bash
cat >> .env << EOF
CF_UID=$(id -u)
CF_GID=$(id -g)
CF_HOME=$HOME
CF_USER=$USER
EOF
```

### Optional: Glances Monitoring

To show host CPU/memory stats in the dashboard, deploy [Glances](https://nicolargo.github.io/glances/) on your hosts and add:

```bash
echo "CF_LOCAL_HOST=nas" >> .env  # Your local hostname
```

See [Host Resource Monitoring](https://github.com/basnijholt/compose-farm#host-resource-monitoring-glances) in the README.

---

## Troubleshooting

### SSH "Permission denied" or "Host key verification failed"

Regenerate keys:

```bash
docker compose run --rm cf ssh setup
```

### Glances shows error for local host

Add your local hostname to `.env`:

```bash
echo "CF_LOCAL_HOST=nas" >> .env
docker compose restart web
```

### Files created as root

Add the non-root variables above and restart.

---

## All Environment Variables

For advanced users, here's the complete reference:

| Variable | Description | Default |
|----------|-------------|---------|
| `DOMAIN` | Domain for Traefik labels | *(required)* |
| `CF_COMPOSE_DIR` | Compose files directory | `/opt/stacks` |
| `CF_UID` / `CF_GID` | User/group ID | `0` (root) |
| `CF_HOME` | Home directory | `/root` |
| `CF_USER` | Username for SSH | `root` |
| `CF_LOCAL_HOST` | Local hostname for Glances | *(auto-detect)* |
| `CF_SSH_DIR` | SSH keys directory | `~/.ssh/compose-farm` |
| `CF_XDG_CONFIG` | Config/backup directory | `~/.config/compose-farm` |
