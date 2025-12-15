# Compose Farm

<img src="http://files.nijho.lt/compose-farm.png" align="right" style="width: 300px;" />

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

> [!NOTE]
> Run `docker compose` commands across multiple hosts via SSH. One YAML maps services to hosts. Change the mapping, run `up`, and it auto-migrates. No Kubernetes, no Swarm, no magic.

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [Why Compose Farm?](#why-compose-farm)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Limitations & Best Practices](#limitations--best-practices)
  - [What breaks when you move a service](#what-breaks-when-you-move-a-service)
  - [Best practices](#best-practices)
  - [What Compose Farm doesn't do](#what-compose-farm-doesnt-do)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Multi-Host Services](#multi-host-services)
- [Usage](#usage)
  - [Auto-Migration](#auto-migration)
- [Traefik Multihost Ingress (File Provider)](#traefik-multihost-ingress-file-provider)
- [License](#license)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Why Compose Farm?

I run 100+ Docker Compose stacks on an LXC container that frequently runs out of memory. I needed a way to distribute services across multiple machines without the complexity of:

- **Kubernetes**: Overkill for my use case. I don't need pods, services, ingress controllers, or YAML manifests 10x the size of my compose files.
- **Docker Swarm**: Effectively in maintenance mode—no longer being invested in by Docker.

**Compose Farm is intentionally simple**: one YAML config mapping services to hosts, and a CLI that runs `docker compose` commands over SSH. That's it.

## How It Works

1. You run `cf up plex`
2. Compose Farm looks up which host runs `plex` (e.g., `server-1`)
3. It SSHs to `server-1` (or runs locally if `localhost`)
4. It executes `docker compose -f /opt/compose/plex/docker-compose.yml up -d`
5. Output is streamed back with `[plex]` prefix

That's it. No orchestration, no service discovery, no magic.

## Requirements

- Python 3.11+ (we recommend [uv](https://docs.astral.sh/uv/) for installation)
- SSH key-based authentication to your hosts (uses ssh-agent)
- Docker and Docker Compose installed on all target hosts
- **Shared storage**: All compose files must be accessible at the same path on all hosts
- **Docker networks**: External networks must exist on all hosts (use `cf init-network` to create)

Compose Farm assumes your compose files are accessible at the same path on all hosts. This is typically achieved via:

- **NFS mount** (e.g., `/opt/compose` mounted from a NAS)
- **Synced folders** (e.g., Syncthing, rsync)
- **Shared filesystem** (e.g., GlusterFS, Ceph)

```
# Example: NFS mount on all Docker hosts
nas:/volume1/compose  →  /opt/compose (on server-1)
nas:/volume1/compose  →  /opt/compose (on server-2)
nas:/volume1/compose  →  /opt/compose (on server-3)
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
uv tool install compose-farm
# or
pip install compose-farm
```

## Configuration

Create `~/.config/compose-farm/compose-farm.yaml` (or `./compose-farm.yaml` in your working directory):

```yaml
compose_dir: /opt/compose  # Must be the same path on all hosts

hosts:
  server-1:
    address: 192.168.1.10
    user: docker
  server-2:
    address: 192.168.1.11
    # user defaults to current user
  local: localhost  # Run locally without SSH

services:
  plex: server-1
  jellyfin: server-2
  sonarr: server-1
  radarr: local  # Runs on the machine where you invoke compose-farm

  # Multi-host services (run on multiple/all hosts)
  autokuma: all              # Runs on ALL configured hosts
  dozzle: [server-1, server-2]  # Explicit list of hosts
```

Compose files are expected at `{compose_dir}/{service}/compose.yaml` (also supports `compose.yml`, `docker-compose.yml`, `docker-compose.yaml`).

### Multi-Host Services

Some services need to run on every host. This is typically required for tools that access **host-local resources** like the Docker socket (`/var/run/docker.sock`), which cannot be accessed remotely without security risks.

Common use cases:
- **AutoKuma** - auto-creates Uptime Kuma monitors from container labels (needs local Docker socket)
- **Dozzle** - real-time log viewer (needs local Docker socket)
- **Promtail/Alloy** - log shipping agents (needs local Docker socket and log files)
- **node-exporter** - Prometheus host metrics (needs access to host /proc, /sys)

This is the same pattern as Docker Swarm's `deploy.mode: global`.

Use the `all` keyword or an explicit list:

```yaml
services:
  # Run on all configured hosts
  autokuma: all
  dozzle: all

  # Run on specific hosts
  node-exporter: [server-1, server-2, server-3]
```

When you run `cf up autokuma`, it starts the service on all hosts in parallel. Multi-host services:
- Are excluded from migration logic (they always run everywhere)
- Show output with `[service@host]` prefix for each host
- Track all running hosts in state

## Usage

The CLI is available as both `compose-farm` and the shorter `cf` alias.

```bash
# Start services (auto-migrates if host changed in config)
cf up plex jellyfin
cf up --all
cf up --migrate        # only services needing migration (state ≠ config)

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

# Validate config, traefik labels, mounts, and networks
cf check                 # full validation (includes SSH checks)
cf check --local         # fast validation (skip SSH)
cf check jellyfin        # check service + show which hosts can run it

# Create Docker network on new hosts (before migrating services)
cf init-network nuc hp   # create mynetwork on specific hosts
cf init-network          # create on all hosts

# View logs
cf logs plex
cf logs -f plex  # follow

# Show status
cf ps
```

### Auto-Migration

When you change a service's host assignment in config and run `up`, Compose Farm automatically:
1. Checks that required mounts and networks exist on the new host (aborts if missing)
2. Runs `down` on the old host
3. Runs `up -d` on the new host
4. Updates state tracking

Use `cf up --migrate` (or `-m`) to automatically find and migrate all services where the current state differs from config—no need to list them manually.

```yaml
# Before: plex runs on server-1
services:
  plex: server-1

# After: change to server-2, then run `cf up plex`
services:
  plex: server-2  # Compose Farm will migrate automatically
```

## Traefik Multihost Ingress (File Provider)

If you run a single Traefik instance on one "front‑door" host and want it to route to
Compose Farm services on other hosts, Compose Farm can generate a Traefik file‑provider
fragment from your existing compose labels.

**How it works**

- Your `docker-compose.yml` remains the source of truth. Put normal `traefik.*` labels on
  the container you want exposed.
- Labels and port specs may use `${VAR}` / `${VAR:-default}`; Compose Farm resolves these
  using the stack's `.env` file and your current environment, just like Docker Compose.
- Publish a host port for that container (via `ports:`). The generator prefers
  host‑published ports so Traefik can reach the service across hosts; if none are found,
  it warns and you'd need L3 reachability to container IPs.
- If a router label doesn't specify `traefik.http.routers.<name>.service` and there's only
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
  traefik: server-1  # Traefik runs here
  plex: server-2     # Services on other hosts get file-provider entries
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

## License

MIT
