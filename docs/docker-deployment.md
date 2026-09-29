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
cf config init-env
```

This auto-detects settings from your `compose-farm.yaml`:
- `DOMAIN` from existing traefik labels
- `CF_COMPOSE_DIR` from config
- `CF_UID/GID/HOME/USER` from current user

It also generates `CF_WEB_PASSWORD`, the web UI login (user `admin`), and prints it
once. The login is required because the container is reached through a
non-loopback Docker network. Save it in your password manager before starting the UI.
Re-running `init-env` rewrites the whole file and keeps only an existing
`CF_WEB_PASSWORD` and `CF_WEB_USERNAME`, exactly as written.

Upgrading an older setup? Download the current `docker-compose.yml` too. Older files
don't pass these variables to the container (`CF_WEB_PASSWORD` needs v1.22.0 or later,
`CF_WEB_NO_AUTH` v1.22.1 or later), so the web UI answers 403 until you do.

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

If auto-detection fails for any value, edit the `.env` file manually.

### Glances Monitoring

To show host CPU/memory stats in the dashboard, deploy [Glances](https://nicolargo.github.io/glances/) on your hosts. When running the web UI container, Compose Farm infers the local host from `CF_WEB_STACK` and uses the Glances container name for that host.

See [Host Resource Monitoring](https://github.com/basnijholt/compose-farm#host-resource-monitoring-glances) in the README.

---

## Troubleshooting

### SSH "Permission denied"

Regenerate keys:

```bash
docker compose run --rm cf ssh setup
```

### SSH "Host key verification failed"

For a new host, verify its fingerprint out-of-band and enroll it without
changing authentication keys:

```bash
docker compose run --rm cf ssh setup --trust-only
```

If a host key legitimately changed, verify the new fingerprint first, remove
only that host's old entry, then rerun the command above. Use `HOST` for port 22
or `[HOST]:PORT` for a custom port:

```bash
ssh-keygen -R HOST -f ~/.ssh/compose-farm/known_hosts
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
| `CF_USER` | Default SSH username for hosts without an explicit `user` | `root` |
| `CF_WEB_STACK` | Web UI stack name (enables self-update, local host inference) | *(none)* |
| `CF_WEB_USERNAME` | Web UI login username | `admin` |
| `CF_WEB_PASSWORD` | Web UI login password required for remote access | *(none)* |
| `CF_WEB_NO_AUTH` | Allow remote passwordless access behind a trusted access layer | *(disabled)* |
| `CF_SSH_DIR` | SSH keys directory | `~/.ssh/compose-farm` |
| `CF_XDG_CONFIG` | Config/backup directory | `~/.config/compose-farm` |
