# Compose Farm

<img src="http://files.nijho.lt/compose-farm.png" align="right" style="width: 300px;" />

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

> [!NOTE]
> Run `docker compose` commands across multiple hosts via SSH. One YAML maps services to hosts. Change the mapping, run `up`, and it auto-migrates. No Kubernetes, no Swarm, no magic.

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [Why Compose Farm?](#why-compose-farm)
- [Key Assumption: Shared Storage](#key-assumption-shared-storage)
- [Limitations & Best Practices](#limitations--best-practices)
  - [What breaks when you move a service](#what-breaks-when-you-move-a-service)
  - [Best practices](#best-practices)
  - [What Compose Farm doesn't do](#what-compose-farm-doesnt-do)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Auto-Migration](#auto-migration)
- [Traefik Multihost Ingress (File Provider)](#traefik-multihost-ingress-file-provider)
- [Requirements](#requirements)
- [How It Works](#how-it-works)
- [License](#license)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

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

## Limitations & Best Practices

Compose Farm moves containers between hosts but **does not provide cross-host networking**. Docker's internal DNS and networks don't span hosts.

### What breaks when you move a service

- **Docker DNS** - `http://redis:6379` won't resolve from another host
- **Docker networks** - Containers can't reach each other via network names
- **Environment variables** - `DATABASE_URL=postgres://db:5432` stops working

### Best practices

1. **Keep dependent services together** - If an app needs a database, redis, or worker, keep them in the same compose file on the same host

2. **Only migrate standalone services** - Services that don't talk to other containers (or only talk to external APIs) are safe to move

3. **Expose ports for cross-host communication** - If services must communicate across hosts, publish ports and use IP addresses instead of container names:
   ```yaml
   # Instead of: DATABASE_URL=postgres://db:5432
   # Use:        DATABASE_URL=postgres://192.168.1.66:5432
   ```
   This includes Traefik routing—containers need published ports for the file-provider to reach them

### What Compose Farm doesn't do

- No overlay networking (use Docker Swarm or Kubernetes for that)
- No service discovery across hosts
- No automatic dependency tracking between compose files

If you need containers on different hosts to communicate seamlessly, you need Docker Swarm, Kubernetes, or a service mesh—which adds the complexity Compose Farm is designed to avoid.

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

Compose files are expected at `{compose_dir}/{service}/compose.yaml` (also supports `compose.yml`, `docker-compose.yml`, `docker-compose.yaml`).

## Usage

The CLI is available as both `compose-farm` and the shorter `cf` alias.

```bash
# Start services (auto-migrates if host changed in config)
cf up plex jellyfin
cf up --all

# Stop services
cf down plex

# Pull latest images
cf pull --all

# Restart (down + up)
cf restart plex

# Update (pull + down + up) - the end-to-end update command
cf update --all

# Sync state with reality (discovers running services + captures image digests)
cf sync              # updates state.yaml and dockerfarm-log.toml
cf sync --dry-run    # preview without writing

# Check config vs disk (find missing services, validate traefik labels)
cf check

# View logs
cf logs plex
cf logs -f plex  # follow

# Show status
cf ps
```

### Auto-Migration

When you change a service's host assignment in config and run `up`, Compose Farm automatically:
1. Runs `down` on the old host
2. Runs `up -d` on the new host
3. Updates state tracking

```yaml
# Before: plex runs on nas01
services:
  plex: nas01

# After: change to nas02, then run `cf up plex`
services:
  plex: nas02  # Compose Farm will migrate automatically
```

## Traefik Multihost Ingress (File Provider)

If you run a single Traefik instance on one “front‑door” host and want it to route to
Compose Farm services on other hosts, Compose Farm can generate a Traefik file‑provider
fragment from your existing compose labels.

**How it works**

- Your `docker-compose.yml` remains the source of truth. Put normal `traefik.*` labels on
  the container you want exposed.
- Labels and port specs may use `${VAR}` / `${VAR:-default}`; Compose Farm resolves these
  using the stack’s `.env` file and your current environment, just like Docker Compose.
- Publish a host port for that container (via `ports:`). The generator prefers
  host‑published ports so Traefik can reach the service across hosts; if none are found,
  it warns and you’d need L3 reachability to container IPs.
- If a router label doesn’t specify `traefik.http.routers.<name>.service` and there’s only
  one Traefik service defined on that container, Compose Farm wires the router to it.
- `compose-farm.yaml` stays unchanged: just `hosts` and `services: service → host`.

Example `docker-compose.yml` pattern:

```yaml
services:
  plex:
    ports: ["32400:32400"]
    labels:
      - traefik.enable=true
      - traefik.http.routers.plex.rule=Host(`plex.lab.mydomain.org`)
      - traefik.http.routers.plex.entrypoints=websecure
      - traefik.http.routers.plex.tls.certresolver=letsencrypt
      - traefik.http.services.plex.loadbalancer.server.port=32400
```

**One‑time Traefik setup**

Enable a file provider watching a directory (any path is fine; a common choice is on your
shared/NFS mount):

```yaml
providers:
  file:
    directory: /mnt/data/traefik/dynamic.d
    watch: true
```

**Generate the fragment**

```bash
cf traefik-file --all --output /mnt/data/traefik/dynamic.d/compose-farm.yml
```

Re‑run this after changing Traefik labels, moving a service to another host, or changing
published ports.

**Auto-regeneration**

To automatically regenerate the Traefik config after `up`, `down`, `restart`, or `update`,
add `traefik_file` to your config:

```yaml
compose_dir: /opt/compose
traefik_file: /opt/traefik/dynamic.d/compose-farm.yml  # auto-regenerate on up/down/restart/update
traefik_service: traefik  # skip services on same host (docker provider handles them)

hosts:
  # ...
services:
  traefik: nas01  # Traefik runs here
  plex: nas02     # Services on other hosts get file-provider entries
  # ...
```

The `traefik_service` option specifies which service runs Traefik. Services on the same host
are skipped in the file-provider config since Traefik's docker provider handles them directly.

Now `cf up plex` will update the Traefik config automatically—no separate
`traefik-file` command needed.

**Combining with existing config**

If you already have a `dynamic.yml` with manual routes, middlewares, etc., move it into the
directory and Traefik will merge all files:

```bash
mkdir -p /opt/traefik/dynamic.d
mv /opt/traefik/dynamic.yml /opt/traefik/dynamic.d/manual.yml
cf traefik-file --all -o /opt/traefik/dynamic.d/compose-farm.yml
```

Update your Traefik config to use directory watching instead of a single file:

```yaml
# Before
- --providers.file.filename=/dynamic.yml

# After
- --providers.file.directory=/dynamic.d
- --providers.file.watch=true
```

## Requirements

- Python 3.11+
- SSH key-based authentication to your hosts (uses ssh-agent)
- Docker and Docker Compose installed on all target hosts
- **Shared storage**: All compose files at the same path on all hosts (NFS, Syncthing, etc.)

## How It Works

1. You run `cf up plex`
2. Compose Farm looks up which host runs `plex` (e.g., `nas01`)
3. It SSHs to `nas01` (or runs locally if `localhost`)
4. It executes `docker compose -f /opt/compose/plex/docker-compose.yml up -d`
5. Output is streamed back with `[plex]` prefix

That's it. No orchestration, no service discovery, no magic.

## License

MIT
