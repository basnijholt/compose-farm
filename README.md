# Compose Farm

[![PyPI](https://img.shields.io/pypi/v/compose-farm)](https://pypi.org/project/compose-farm/)
[![Python](https://img.shields.io/pypi/pyversions/compose-farm)](https://pypi.org/project/compose-farm/)
[![License](https://img.shields.io/github/license/basnijholt/compose-farm)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/basnijholt/compose-farm)](https://github.com/basnijholt/compose-farm/stargazers)

<img src="http://files.nijho.lt/compose-farm.png" align="right" style="width: 300px;" />

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

> [!NOTE]
> Run `docker compose` commands across multiple hosts via SSH. One YAML maps services to hosts. Run `cf apply` and reality matches your config—services start, migrate, or stop as needed. No Kubernetes, no Swarm, no magic.

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
  - [Config Command](#config-command)
- [Usage](#usage)
  - [Auto-Migration](#auto-migration)
- [Traefik Multihost Ingress (File Provider)](#traefik-multihost-ingress-file-provider)
- [Comparison with Alternatives](#comparison-with-alternatives)
- [License](#license)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Why Compose Farm?

I used to run 100+ Docker Compose stacks on a single machine that kept running out of memory. I needed a way to distribute services across multiple machines without the complexity of:

- **Kubernetes**: Overkill for my use case. I don't need pods, services, ingress controllers, or YAML manifests 10x the size of my compose files.
- **Docker Swarm**: Effectively in maintenance mode—no longer being invested in by Docker.

Both require changes to your compose files. **Compose Farm requires zero changes**—your existing `docker-compose.yml` files work as-is.

I also wanted a declarative setup—one config file that defines where everything runs. Change the config, run `cf apply`, and everything reconciles—services start, migrate, or stop as needed. See [Comparison with Alternatives](#comparison-with-alternatives) for how this compares to other approaches.

<p align="center">
<a href="https://xkcd.com/927/">
<img src="https://imgs.xkcd.com/comics/standards.png" alt="xkcd: Standards" width="400" />
</a>
</p>

Before you say it—no, this is not a new standard. I changed nothing about my existing setup. When I added more hosts, I just mounted my drives at the same paths, and everything worked. You can do all of this manually today—SSH into a host and run `docker compose up`.

Compose Farm just automates what you'd do by hand:
- Runs `docker compose` commands over SSH
- Tracks which service runs on which host
- **One command (`cf apply`) to reconcile everything**—start missing services, migrate moved ones, stop removed ones
- Generates Traefik file-provider config for cross-host routing

**It's a convenience wrapper, not a new paradigm.**

## How It Works

**The declarative way** — run `cf apply` and reality matches your config:

1. Compose Farm compares your config to what's actually running
2. Services in config but not running? **Starts them**
3. Services on the wrong host? **Migrates them** (stops on old host, starts on new)
4. Services running but removed from config? **Stops them**

**Under the hood** — each service operation is just SSH + docker compose:

1. Look up which host runs the service (e.g., `plex` → `server-1`)
2. SSH to `server-1` (or run locally if `localhost`)
3. Execute `docker compose -f /opt/compose/plex/docker-compose.yml up -d`
4. Stream output back with `[plex]` prefix

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

<details><summary>🐳 Docker</summary>

Using the provided `docker-compose.yml`:
```bash
docker compose run --rm cf up --all
```

Or directly:
```bash
docker run --rm \
  -v $SSH_AUTH_SOCK:/ssh-agent -e SSH_AUTH_SOCK=/ssh-agent \
  -v ./compose-farm.yaml:/root/.config/compose-farm/compose-farm.yaml:ro \
  ghcr.io/basnijholt/compose-farm up --all
```

</details>

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

### Config Command

Compose Farm includes a `config` subcommand to help manage configuration files:

```bash
cf config init      # Create a new config file with documented example
cf config show      # Display current config with syntax highlighting
cf config path      # Print the config file path (useful for scripting)
cf config validate  # Validate config syntax and schema
cf config edit      # Open config in $EDITOR
```

Use `cf config init` to get started with a fully documented template.

## Usage

The CLI is available as both `compose-farm` and the shorter `cf` alias.

| Command | Description |
|---------|-------------|
| **`cf apply`** | **Make reality match config (start + migrate + stop orphans)** |
| `cf up <svc>` | Start service (auto-migrates if host changed) |
| `cf down <svc>` | Stop service |
| `cf restart <svc>` | down + up |
| `cf update <svc>` | pull + down + up |
| `cf pull <svc>` | Pull latest images |
| `cf logs -f <svc>` | Follow logs |
| `cf ps` | Show status of all services |
| `cf refresh` | Update state from running services |
| `cf check` | Validate config, mounts, networks |
| `cf init-network` | Create Docker network on hosts |
| `cf traefik-file` | Generate Traefik file-provider config |
| `cf config <cmd>` | Manage config files (init, show, path, validate, edit) |

All commands support `--all` to operate on all services.

Each command replaces: look up host → SSH → find compose file → run `ssh host "cd /opt/compose/plex && docker compose up -d"`.

```bash
# The main command: make reality match your config
cf apply               # start missing + migrate + stop orphans
cf apply --dry-run     # preview what would change
cf apply --no-orphans  # skip stopping orphaned services
cf apply --full        # also refresh all services (picks up config changes)

# Or operate on individual services
cf up plex jellyfin    # start services (auto-migrates if host changed)
cf up --all
cf down plex           # stop services
cf down --orphaned     # stop services removed from config

# Pull latest images
cf pull --all

# Restart (down + up)
cf restart plex

# Update (pull + down + up) - the end-to-end update command
cf update --all

# Update state from reality (discovers running services + captures digests)
cf refresh             # updates state.yaml and dockerfarm-log.toml
cf refresh --dry-run   # preview without writing

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

<details>
<summary>See the output of <code>cf --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf [OPTIONS] COMMAND [ARGS]...

 Compose Farm - run docker compose commands across multiple hosts

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --version             -v        Show version and exit                        │
│ --install-completion            Install completion for the current shell.    │
│ --show-completion               Show completion for the current shell, to    │
│                                 copy it or customize the installation.       │
│ --help                -h        Show this message and exit.                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Lifecycle ──────────────────────────────────────────────────────────────────╮
│ up             Start services (docker compose up -d). Auto-migrates if host  │
│                changed.                                                      │
│ down           Stop services (docker compose down).                          │
│ pull           Pull latest images (docker compose pull).                     │
│ restart        Restart services (down + up).                                 │
│ update         Update services (pull + build + down + up).                   │
│ apply          Make reality match config (start, migrate, stop as needed).   │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Configuration ──────────────────────────────────────────────────────────────╮
│ traefik-file   Generate a Traefik file-provider fragment from compose        │
│                Traefik labels.                                               │
│ refresh        Update local state from running services.                     │
│ check          Validate configuration, traefik labels, mounts, and networks. │
│ init-network   Create Docker network on hosts with consistent settings.      │
│ config         Manage compose-farm configuration files.                      │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Monitoring ─────────────────────────────────────────────────────────────────╮
│ logs           Show service logs.                                            │
│ ps             Show status of all services.                                  │
│ stats          Show overview statistics for hosts and services.              │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

### Auto-Migration

When you change a service's host assignment in config and run `up`, Compose Farm automatically:
1. Checks that required mounts and networks exist on the new host (aborts if missing)
2. Runs `down` on the old host
3. Runs `up -d` on the new host
4. Updates state tracking

Use `cf apply` to automatically reconcile all services—it finds and migrates services on wrong hosts, stops orphaned services, and starts missing services.

```yaml
# Before: plex runs on server-1
services:
  plex: server-1

# After: change to server-2, then run `cf up plex`
services:
  plex: server-2  # Compose Farm will migrate automatically
```

**Orphaned services**: When you remove (or comment out) a service from config, it becomes "orphaned"—tracked in state but no longer in config. Use these commands to handle orphans:

- `cf apply` — Migrate services AND stop orphans (the full reconcile)
- `cf down --orphaned` — Only stop orphaned services
- `cf apply --dry-run` — Preview what would change before applying

This makes the config truly declarative: comment out a service, run `cf apply`, and it stops.

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

## Comparison with Alternatives

There are many ways to run containers on multiple hosts. Here is where Compose Farm sits:

| | Compose Farm | Docker Contexts | K8s / Swarm | Ansible / Terraform | Portainer / Coolify |
|---|:---:|:---:|:---:|:---:|:---:|
| No compose rewrites | ✅ | ✅ | ❌ | ✅ | ✅ |
| Version controlled | ✅ | ✅ | ✅ | ✅ | ❌ |
| State tracking | ✅ | ❌ | ✅ | ✅ | ✅ |
| Auto-migration | ✅ | ❌ | ✅ | ❌ | ❌ |
| Interactive CLI | ✅ | ❌ | ❌ | ❌ | ❌ |
| Parallel execution | ✅ | ❌ | ✅ | ✅ | ✅ |
| Agentless | ✅ | ✅ | ❌ | ✅ | ❌ |
| High availability | ❌ | ❌ | ✅ | ❌ | ❌ |

**Docker Contexts** — You can use `docker context create remote ssh://...` and `docker compose --context remote up`. But it's manual: you must remember which host runs which service, there's no global view, no parallel execution, and no auto-migration.

**Kubernetes / Docker Swarm** — Full orchestration that abstracts away the hardware. But they require cluster initialization, separate control planes, and often rewriting compose files. They introduce complexity (consensus, overlay networks) unnecessary for static "pet" servers.

**Ansible / Terraform** — Infrastructure-as-Code tools that can SSH in and deploy containers. But they're push-based configuration management, not interactive CLIs. Great for setting up state, clumsy for day-to-day operations like `cf logs -f` or quickly restarting a service.

**Portainer / Coolify** — Web-based management UIs. But they're UI-first and often require agents on your servers. Compose Farm is CLI-first and agentless.

**Compose Farm is the middle ground:** a robust CLI that productizes the manual SSH pattern. You get the "cluster feel" (unified commands, state tracking) without the "cluster cost" (complexity, agents, control planes).

## License

MIT
