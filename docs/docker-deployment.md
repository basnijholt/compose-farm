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

**2. Generate `.env` file:**

```bash
cf config init-env -o .env
```

This auto-detects settings from your `compose-farm.yaml`:
- `DOMAIN` from existing traefik labels
- `CF_COMPOSE_DIR` from config
- `CF_UID/GID/HOME/USER` from current user
- `CF_LOCAL_HOST` by matching local IPs to config hosts

Review the output and edit if needed.

**3. Set up SSH keys:**

```bash
docker compose run --rm cf ssh setup
```

**4. Start the web UI:**

```bash
docker compose up -d web
```

Open `http://localhost:9000` (or `https://compose-farm.example.com` if using Traefik).

---

## Configuration

The `cf config init-env` command auto-detects most settings. After running it, review the generated `.env` file and edit if needed:

```bash
$EDITOR .env
```

### What init-env detects

| Variable | How it's detected |
|----------|-------------------|
| `DOMAIN` | Extracted from traefik labels in your stacks |
| `CF_COMPOSE_DIR` | From `compose_dir` in your config |
| `CF_UID/GID/HOME/USER` | From current user (for NFS compatibility) |
| `CF_LOCAL_HOST` | By matching local IPs to configured hosts |

If auto-detection fails for any value, edit the `.env` file manually.

### Glances Monitoring

To show host CPU/memory stats in the dashboard, deploy [Glances](https://nicolargo.github.io/glances/) on your hosts. If `CF_LOCAL_HOST` wasn't detected correctly, set it to your local hostname:

```bash
CF_LOCAL_HOST=nas  # Replace with your local host name
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
