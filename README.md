# Compose Farm

[![PyPI](https://img.shields.io/pypi/v/compose-farm)](https://pypi.org/project/compose-farm/)
[![Python](https://img.shields.io/pypi/pyversions/compose-farm)](https://pypi.org/project/compose-farm/)
[![License](https://img.shields.io/github/license/basnijholt/compose-farm)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/basnijholt/compose-farm)](https://github.com/basnijholt/compose-farm/stargazers)

<img src="https://files.nijho.lt/compose-farm.png" alt="Compose Farm logo" align="right" style="width: 300px;" />

A minimal CLI tool to run Docker Compose commands across multiple hosts via SSH.

> [!NOTE]
> Agentless multi-host Docker Compose. CLI-first with a web UI. Your files stay as plain folders—version-controllable, no lock-in. Run `cf apply` and reality matches your config.

**Why Compose Farm?**
- **Your files, your control** — Plain folders + YAML, not locked in Portainer. Version control everything.
- **Agentless** — Just SSH, no agents to deploy (unlike [Dockge](https://github.com/louislam/dockge)).
- **Zero changes required** — Existing compose files work as-is.
- **Grows with you** — Start single-host, scale to multi-host seamlessly.
- **Declarative** — Change config, run `cf apply`, reality matches.

## Quick Demo

**CLI:**

![CLI Demo](docs/assets/quickstart.gif)

**Web UI:**

![Web UI Demo](docs/assets/web-workflow.gif)

## Table of Contents

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [Why Compose Farm?](#why-compose-farm)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Limitations & Best Practices](#limitations--best-practices)
  - [What breaks when you move a stack](#what-breaks-when-you-move-a-stack)
  - [Best practices](#best-practices)
  - [What Compose Farm doesn't do](#what-compose-farm-doesnt-do)
- [Installation](#installation)
- [SSH Authentication](#ssh-authentication)
  - [SSH Agent](#ssh-agent)
  - [Dedicated SSH Key (default for Docker)](#dedicated-ssh-key-default-for-docker)
- [Configuration](#configuration)
  - [Single-host example](#single-host-example)
  - [Multi-host example](#multi-host-example)
  - [Multi-Host Stacks](#multi-host-stacks)
  - [Config Command](#config-command)
- [Usage](#usage)
  - [CLI `--help` Output](#cli---help-output)
  - [Auto-Migration](#auto-migration)
- [Traefik Multihost Ingress (File Provider)](#traefik-multihost-ingress-file-provider)
- [Host Resource Monitoring (Glances)](#host-resource-monitoring-glances)
- [Comparison with Alternatives](#comparison-with-alternatives)
- [License](#license)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Why Compose Farm?

I used to run 100+ Docker Compose stacks on a single machine that kept running out of memory. I needed a way to distribute stacks across multiple machines without the complexity of:

- **Kubernetes**: Overkill for my use case. I don't need pods, services, ingress controllers, or YAML manifests 10x the size of my compose files.
- **Docker Swarm**: Effectively in maintenance mode—no longer being invested in by Docker.

Both require changes to your compose files. **Compose Farm requires zero changes**—your existing `docker-compose.yml` files work as-is.

I also wanted a declarative setup—one config file that defines where everything runs. Change the config, run `cf apply`, and everything reconciles—stacks start, migrate, or stop as needed. See [Comparison with Alternatives](#comparison-with-alternatives) for how this compares to other approaches.

<p align="center">
<a href="https://xkcd.com/927/">
<img src="https://imgs.xkcd.com/comics/standards.png" alt="xkcd: Standards" width="400" />
</a>
</p>

Before you say it—no, this is not a new standard. I changed nothing about my existing setup. When I added more hosts, I just mounted my drives at the same paths, and everything worked. You can do all of this manually today—SSH into a host and run `docker compose up`.

Compose Farm just automates what you'd do by hand:
- Runs `docker compose` commands over SSH
- Tracks which stack runs on which host
- **One command (`cf apply`) to reconcile everything**—start missing stacks, migrate moved ones, stop removed ones
- Generates Traefik file-provider config for cross-host routing

**It's a convenience wrapper, not a new paradigm.**

## How It Works

**The declarative way** — run `cf apply` and reality matches your config:

1. Compose Farm compares your config to what's actually running
2. Stacks in config but not running? **Starts them**
3. Stacks on the wrong host? **Migrates them** (stops on old host, starts on new)
4. Stacks running but removed from config? **Stops them**

**Under the hood** — each stack operation is just SSH + docker compose:

1. Look up which host runs the stack (e.g., `plex` → `server-1`)
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

Compose Farm simply runs `docker compose -f /opt/compose/{stack}/docker-compose.yml` on the appropriate host—it doesn't copy or sync files.

## Limitations & Best Practices

Compose Farm moves containers between hosts but **does not provide cross-host networking**. Docker's internal DNS and networks don't span hosts.

### What breaks when you move a stack

- **Docker DNS** - `http://redis:6379` won't resolve from another host
- **Docker networks** - Containers can't reach each other via network names
- **Environment variables** - `DATABASE_URL=postgres://db:5432` stops working

### Best practices

1. **Keep dependent services together** - If an app needs a database, redis, or worker, keep them in the same compose file on the same host

2. **Only migrate standalone stacks** - Stacks whose services don't talk to other containers (or only talk to external APIs) are safe to move

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
# One-liner (installs uv if needed)
curl -fsSL https://compose-farm.nijho.lt/install | sh

# Or if you already have uv/pip
uv tool install compose-farm
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

**Running as non-root user** (recommended for NFS mounts):

By default, containers run as root. To preserve file ownership on mounted volumes
(e.g., `compose-farm-state.yaml`, config edits), set these environment variables:

```bash
# Add to .env file (one-time setup)
echo "CF_UID=$(id -u)" >> .env
echo "CF_GID=$(id -g)" >> .env
echo "CF_HOME=$HOME" >> .env
echo "CF_USER=$USER" >> .env
```

Or use [direnv](https://direnv.net/) (copies `.envrc.example` to `.envrc`):
```bash
cp .envrc.example .envrc && direnv allow
```

</details>

## SSH Authentication

Compose Farm uses SSH to run commands on remote hosts. There are two authentication methods:

### SSH Agent

Works out of the box when running locally if you have an SSH agent running with your keys loaded:

```bash
# Verify your agent has keys
ssh-add -l

# Run compose-farm commands
cf up --all
```

### Dedicated SSH Key (default for Docker)

When running in Docker, SSH agent sockets are ephemeral and can be lost after container restarts. The `cf ssh` command sets up a dedicated key that persists:

```bash
# Generate key and copy to all configured hosts
cf ssh setup

# Check status
cf ssh status
```

This creates `~/.ssh/compose-farm/id_ed25519` (ED25519, no passphrase) and copies the public key to each host's `authorized_keys`. Compose Farm tries the SSH agent first, then falls back to this key.

<details><summary>🐳 Docker volume options for SSH keys</summary>

When running in Docker, mount a volume to persist the SSH keys. Choose ONE option and use it for both `cf` and `web` Compose services:

**Option 1: Host path (default)** - keys at `~/.ssh/compose-farm/id_ed25519`
```yaml
volumes:
  - ~/.ssh/compose-farm:${CF_HOME:-/root}/.ssh
```

**Option 2: Named volume** - managed by Docker
```yaml
volumes:
  - cf-ssh:${CF_HOME:-/root}/.ssh
```

**Option 3: SSH agent forwarding** - if you prefer using your host's ssh-agent
```yaml
volumes:
  - ${SSH_AUTH_SOCK}:/ssh-agent:ro
```
Note: Requires `SSH_AUTH_SOCK` environment variable to be set. The socket path is ephemeral and changes across sessions.

Run setup once after starting the container (while the SSH agent still works):

```bash
docker compose exec web cf ssh setup
```

The keys will persist across restarts.

**Note:** When running as non-root (with `CF_UID`/`CF_GID`), set `CF_HOME` to your home directory so SSH finds the keys at the correct path.

</details>

## Configuration

Create `compose-farm.yaml` in the directory where you'll run commands (e.g., `/opt/stacks`). This keeps config near your stacks. Alternatively, use `~/.config/compose-farm/compose-farm.yaml` for a global config, or symlink from one to the other with `cf config symlink`.

### Single-host example

No SSH, shared storage, or Traefik file-provider required.

```yaml
compose_dir: /opt/stacks

hosts:
  local: localhost  # Run locally without SSH

stacks:
  plex: local
  jellyfin: local
  traefik: local
```

### Multi-host example
```yaml
compose_dir: /opt/compose  # Must be the same path on all hosts

hosts:
  server-1:
    address: 192.168.1.10
    user: docker
  server-2:
    address: 192.168.1.11
    # user defaults to current user

stacks:
  plex: server-1
  jellyfin: server-2
  grafana: server-1

  # Multi-host stacks (run on multiple/all hosts)
  autokuma: all              # Runs on ALL configured hosts
  dozzle: [server-1, server-2]  # Explicit list of hosts
```

For cross-host HTTP routing, add Traefik labels to your compose files and set `traefik_file` so Compose Farm can generate the file-provider config.

Each entry in `stacks:` maps to a folder under `compose_dir` that contains a compose file. Compose files are expected at `{compose_dir}/{stack}/compose.yaml` (also supports `compose.yml`, `docker-compose.yml`, `docker-compose.yaml`).

### Multi-Host Stacks

Some stacks need to run on every host. This is typically required for tools that access **host-local resources** like the Docker socket (`/var/run/docker.sock`), which cannot be accessed remotely without security risks.

Common use cases:
- **AutoKuma** - auto-creates Uptime Kuma monitors from container labels (needs local Docker socket)
- **Dozzle** - real-time log viewer (needs local Docker socket)
- **Promtail/Alloy** - log shipping agents (needs local Docker socket and log files)
- **node-exporter** - Prometheus host metrics (needs access to host /proc, /sys)

This is the same pattern as Docker Swarm's `deploy.mode: global`.

Use the `all` keyword or an explicit list:

```yaml
stacks:
  # Run on all configured hosts
  autokuma: all
  dozzle: all

  # Run on specific hosts
  node-exporter: [server-1, server-2, server-3]
```

When you run `cf up autokuma`, it starts the stack on all hosts in parallel. Multi-host stacks:
- Are excluded from migration logic (they always run everywhere)
- Show output with `[stack@host]` prefix for each host
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
| `cf up <stack>` | Start stack (auto-migrates if host changed) |
| `cf down <stack>` | Stop and remove stack containers |
| `cf stop <stack>` | Stop stack without removing containers |
| `cf restart <stack>` | down + up |
| `cf update <stack>` | pull + build + down + up |
| `cf pull <stack>` | Pull latest images |
| `cf logs -f <stack>` | Follow logs |
| `cf ps` | Show status of all stacks |
| `cf refresh` | Update state from running stacks |
| `cf check` | Validate config, mounts, networks |
| `cf init-network` | Create Docker network on hosts |
| `cf traefik-file` | Generate Traefik file-provider config |
| `cf config <cmd>` | Manage config files (init, show, path, validate, edit, symlink) |

All commands support `--all` to operate on all stacks.

Each command replaces: look up host → SSH → find compose file → run `ssh host "cd /opt/compose/plex && docker compose up -d"`.

```bash
# The main command: make reality match your config
cf apply               # start missing + migrate + stop orphans
cf apply --dry-run     # preview what would change
cf apply --no-orphans  # skip stopping orphaned stacks
cf apply --full        # also refresh all stacks (picks up config changes)

# Or operate on individual stacks
cf up plex jellyfin    # start stacks (auto-migrates if host changed)
cf up --all
cf down plex           # stop stacks
cf down --orphaned     # stop stacks removed from config

# Pull latest images
cf pull --all

# Restart (down + up)
cf restart plex

# Update (pull + build + down + up) - the end-to-end update command
cf update --all

# Update state from reality (discovers running stacks + captures digests)
cf refresh             # updates compose-farm-state.yaml and dockerfarm-log.toml
cf refresh --dry-run   # preview without writing

# Validate config, traefik labels, mounts, and networks
cf check                 # full validation (includes SSH checks)
cf check --local         # fast validation (skip SSH)
cf check jellyfin        # check stack + show which hosts can run it

# Create Docker network on new hosts (before migrating stacks)
cf init-network nuc hp   # create mynetwork on specific hosts
cf init-network          # create on all hosts

# View logs
cf logs plex
cf logs -f plex  # follow

# Show status
cf ps
```

### CLI `--help` Output

Full `--help` output for each command. See the [Usage](#usage) table above for a quick overview.

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
╭─ Configuration ──────────────────────────────────────────────────────────────╮
│ traefik-file   Generate a Traefik file-provider fragment from compose        │
│                Traefik labels.                                               │
│ refresh        Update local state from running stacks.                       │
│ check          Validate configuration, traefik labels, mounts, and networks. │
│ init-network   Create Docker network on hosts with consistent settings.      │
│ config         Manage compose-farm configuration files.                      │
│ ssh            Manage SSH keys for passwordless authentication.              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Lifecycle ──────────────────────────────────────────────────────────────────╮
│ up             Start stacks (docker compose up -d). Auto-migrates if host    │
│                changed.                                                      │
│ down           Stop stacks (docker compose down).                            │
│ stop           Stop services without removing containers (docker compose     │
│                stop).                                                        │
│ pull           Pull latest images (docker compose pull).                     │
│ restart        Restart stacks (down + up). With --service, restarts just     │
│                that service.                                                 │
│ update         Update stacks (pull + build + down + up). With --service,     │
│                updates just that service.                                    │
│ apply          Make reality match config (start, migrate, stop               │
│                strays/orphans as needed).                                    │
│ compose        Run any docker compose command on a stack.                    │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Monitoring ─────────────────────────────────────────────────────────────────╮
│ logs           Show stack logs. With --service, shows logs for just that     │
│                service.                                                      │
│ ps             Show status of stacks.                                        │
│ stats          Show overview statistics for hosts and stacks.                │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Server ─────────────────────────────────────────────────────────────────────╮
│ web            Start the web UI server.                                      │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

**Lifecycle**

<details>
<summary>See the output of <code>cf up --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf up --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf up [OPTIONS] [STACKS]...

 Start stacks (docker compose up -d). Auto-migrates if host changed.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all      -a            Run on all stacks                                   │
│ --host     -H      TEXT  Filter to stacks on this host                       │
│ --service  -s      TEXT  Target a specific service within the stack          │
│ --config   -c      PATH  Path to config file                                 │
│ --help     -h            Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf down --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf down --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf down [OPTIONS] [STACKS]...

 Stop stacks (docker compose down).

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all       -a            Run on all stacks                                  │
│ --orphaned                Stop orphaned stacks (in state but removed from    │
│                           config)                                            │
│ --host      -H      TEXT  Filter to stacks on this host                      │
│ --config    -c      PATH  Path to config file                                │
│ --help      -h            Show this message and exit.                        │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf stop --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf stop --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf stop [OPTIONS] [STACKS]...

 Stop services without removing containers (docker compose stop).

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all      -a            Run on all stacks                                   │
│ --service  -s      TEXT  Target a specific service within the stack          │
│ --config   -c      PATH  Path to config file                                 │
│ --help     -h            Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf pull --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf pull --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf pull [OPTIONS] [STACKS]...

 Pull latest images (docker compose pull).

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all      -a            Run on all stacks                                   │
│ --service  -s      TEXT  Target a specific service within the stack          │
│ --config   -c      PATH  Path to config file                                 │
│ --help     -h            Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf restart --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf restart --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf restart [OPTIONS] [STACKS]...

 Restart stacks (down + up). With --service, restarts just that service.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all      -a            Run on all stacks                                   │
│ --service  -s      TEXT  Target a specific service within the stack          │
│ --config   -c      PATH  Path to config file                                 │
│ --help     -h            Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf update --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf update --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf update [OPTIONS] [STACKS]...

 Update stacks (pull + build + down + up). With --service, updates just that
 service.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all      -a            Run on all stacks                                   │
│ --service  -s      TEXT  Target a specific service within the stack          │
│ --config   -c      PATH  Path to config file                                 │
│ --help     -h            Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf apply --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf apply --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf apply [OPTIONS]

 Make reality match config (start, migrate, stop strays/orphans as needed).

 This is the "reconcile" command that ensures running stacks match your
 config file. It will:

 1. Stop orphaned stacks (in state but removed from config)
 2. Stop stray stacks (running on unauthorized hosts)
 3. Migrate stacks on wrong host (host in state ≠ host in config)
 4. Start missing stacks (in config but not in state)

 Use --dry-run to preview changes before applying.
 Use --no-orphans to skip stopping orphaned stacks.
 Use --no-strays to skip stopping stray stacks.
 Use --full to also run 'up' on all stacks (picks up compose/env changes).

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --dry-run     -n            Show what would change without executing         │
│ --no-orphans                Only migrate, don't stop orphaned stacks         │
│ --no-strays                 Don't stop stray stacks (running on wrong host)  │
│ --full        -f            Also run up on all stacks to apply config        │
│                             changes                                          │
│ --config      -c      PATH  Path to config file                              │
│ --help        -h            Show this message and exit.                      │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf compose --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf compose --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf compose [OPTIONS] STACK COMMAND [ARGS]...

 Run any docker compose command on a stack.

 Passthrough to docker compose for commands not wrapped by cf.
 Options after COMMAND are passed to docker compose, not cf.

 Examples:
   cf compose mystack --help        - show docker compose help
   cf compose mystack top           - view running processes
   cf compose mystack images        - list images
   cf compose mystack exec web bash - interactive shell
   cf compose mystack config        - view parsed config

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    stack        TEXT       Stack to operate on (use '.' for current dir)   │
│                              [required]                                      │
│ *    command      TEXT       Docker compose command [required]               │
│      args         [ARGS]...  Additional arguments                            │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --host    -H      TEXT  Filter to stacks on this host                        │
│ --config  -c      PATH  Path to config file                                  │
│ --help    -h            Show this message and exit.                          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

**Configuration**

<details>
<summary>See the output of <code>cf traefik-file --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf traefik-file --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf traefik-file [OPTIONS] [STACKS]...

 Generate a Traefik file-provider fragment from compose Traefik labels.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all     -a            Run on all stacks                                    │
│ --output  -o      PATH  Write Traefik file-provider YAML to this path        │
│                         (stdout if omitted)                                  │
│ --config  -c      PATH  Path to config file                                  │
│ --help    -h            Show this message and exit.                          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

<details>
<summary>See the output of <code>cf refresh --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf refresh --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf refresh [OPTIONS] [STACKS]...

 Update local state from running stacks.

 Discovers which stacks are running on which hosts, updates the state
 file, and captures image digests. This is a read operation - it updates
 your local state to match reality, not the other way around.

 Without arguments: refreshes all stacks (same as --all).
 With stack names: refreshes only those stacks.

 Use 'cf apply' to make reality match your config (stop orphans, migrate).

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all       -a            Run on all stacks                                  │
│ --config    -c      PATH  Path to config file                                │
│ --log-path  -l      PATH  Path to Dockerfarm TOML log                        │
│ --dry-run   -n            Show what would change without writing             │
│ --help      -h            Show this message and exit.                        │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>


<details>
<summary>See the output of <code>cf check --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf check --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf check [OPTIONS] [STACKS]...

 Validate configuration, traefik labels, mounts, and networks.

 Without arguments: validates all stacks against configured hosts.
 With stack arguments: validates specific stacks and shows host compatibility.

 Use --local to skip SSH-based checks for faster validation.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --local                 Skip SSH-based checks (faster)                       │
│ --config  -c      PATH  Path to config file                                  │
│ --help    -h            Show this message and exit.                          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>


<details>
<summary>See the output of <code>cf init-network --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf init-network --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf init-network [OPTIONS] [HOSTS]...

 Create Docker network on hosts with consistent settings.

 Creates an external Docker network that stacks can use for cross-host
 communication. Uses the same subnet/gateway on all hosts to ensure
 consistent networking.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   hosts      [HOSTS]...  Hosts to create network on (default: all)           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --network  -n      TEXT  Network name [default: mynetwork]                   │
│ --subnet   -s      TEXT  Network subnet [default: 172.20.0.0/16]             │
│ --gateway  -g      TEXT  Network gateway [default: 172.20.0.1]               │
│ --config   -c      PATH  Path to config file                                 │
│ --help     -h            Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>


<details>
<summary>See the output of <code>cf config --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf config --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf config [OPTIONS] COMMAND [ARGS]...

 Manage compose-farm configuration files.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help  -h        Show this message and exit.                                │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ init       Create a new config file with documented example.                 │
│ edit       Open the config file in your default editor.                      │
│ show       Display the config file location and contents.                    │
│ path       Print the config file path (useful for scripting).                │
│ validate   Validate the config file syntax and schema.                       │
│ symlink    Create a symlink from the default config location to a config     │
│            file.                                                             │
│ init-env   Generate a .env file for Docker deployment.                       │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>


<details>
<summary>See the output of <code>cf ssh --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf ssh --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf ssh [OPTIONS] COMMAND [ARGS]...

 Manage SSH keys for passwordless authentication.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help  -h        Show this message and exit.                                │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ keygen   Generate SSH key (does not distribute to hosts).                    │
│ setup    Generate SSH key and distribute to all configured hosts.            │
│ status   Show SSH key status and host connectivity.                          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

**Monitoring**

<details>
<summary>See the output of <code>cf logs --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf logs --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf logs [OPTIONS] [STACKS]...

 Show stack logs. With --service, shows logs for just that service.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all      -a               Run on all stacks                                │
│ --host     -H      TEXT     Filter to stacks on this host                    │
│ --service  -s      TEXT     Target a specific service within the stack       │
│ --follow   -f               Follow logs                                      │
│ --tail     -n      INTEGER  Number of lines (default: 20 for --all, 100      │
│                             otherwise)                                       │
│ --config   -c      PATH     Path to config file                              │
│ --help     -h               Show this message and exit.                      │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>


<details>
<summary>See the output of <code>cf ps --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf ps --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf ps [OPTIONS] [STACKS]...

 Show status of stacks.

 Without arguments: shows all stacks (same as --all).
 With stack names: shows only those stacks.
 With --host: shows stacks on that host.
 With --service: filters to a specific service within the stack.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   stacks      [STACKS]...  Stacks to operate on                              │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --all      -a            Run on all stacks                                   │
│ --host     -H      TEXT  Filter to stacks on this host                       │
│ --service  -s      TEXT  Target a specific service within the stack          │
│ --config   -c      PATH  Path to config file                                 │
│ --help     -h            Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>


<details>
<summary>See the output of <code>cf stats --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf stats --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf stats [OPTIONS]

 Show overview statistics for hosts and stacks.

 Without --live: Shows config/state info (hosts, stacks, pending migrations).
 With --live: Also queries Docker on each host for container counts.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --live    -l            Query Docker for live container stats                │
│ --config  -c      PATH  Path to config file                                  │
│ --help    -h            Show this message and exit.                          │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

**Server**

<details>
<summary>See the output of <code>cf web --help</code></summary>

<!-- CODE:BASH:START -->
<!-- echo '```yaml' -->
<!-- export NO_COLOR=1 -->
<!-- export TERM=dumb -->
<!-- export TERMINAL_WIDTH=90 -->
<!-- cf web --help -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```yaml

 Usage: cf web [OPTIONS]

 Start the web UI server.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --host    -H      TEXT     Host to bind to [default: 0.0.0.0]                │
│ --port    -p      INTEGER  Port to listen on [default: 8000]                 │
│ --reload  -r               Enable auto-reload for development                │
│ --help    -h               Show this message and exit.                       │
╰──────────────────────────────────────────────────────────────────────────────╯

```

<!-- OUTPUT:END -->

</details>

### Auto-Migration

When you change a stack's host assignment in config and run `up`, Compose Farm automatically:
1. Checks that required mounts and networks exist on the new host (aborts if missing)
2. Runs `down` on the old host
3. Runs `up -d` on the new host
4. Updates state tracking

Use `cf apply` to automatically reconcile all stacks—it finds and migrates stacks on wrong hosts, stops orphaned stacks, and starts missing stacks.

```yaml
# Before: plex runs on server-1
stacks:
  plex: server-1

# After: change to server-2, then run `cf up plex`
stacks:
  plex: server-2  # Compose Farm will migrate automatically
```

**Orphaned stacks**: When you remove (or comment out) a stack from config, it becomes "orphaned"—tracked in state but no longer in config. Use these commands to handle orphans:

- `cf apply` — Migrate stacks AND stop orphans (the full reconcile)
- `cf down --orphaned` — Only stop orphaned stacks
- `cf apply --dry-run` — Preview what would change before applying

This makes the config truly declarative: comment out a stack, run `cf apply`, and it stops.

## Traefik Multihost Ingress (File Provider)

If you run a single Traefik instance on one "front‑door" host and want it to route to
Compose Farm stacks on other hosts, Compose Farm can generate a Traefik file‑provider
fragment from your existing compose labels.

**How it works**

- Your `docker-compose.yml` remains the source of truth. Put normal `traefik.*` labels on
  the container you want exposed.
- Labels and port specs may use `${VAR}` / `${VAR:-default}`; Compose Farm resolves these
  using the stack's `.env` file and your current environment, just like Docker Compose.
- Publish a host port for that container (via `ports:`). The generator prefers
  host‑published ports so Traefik can reach the stack across hosts; if none are found,
  it warns and you'd need L3 reachability to container IPs.
- If a router label doesn't specify `traefik.http.routers.<name>.service` and there's only
  one Traefik service defined on that container, Compose Farm wires the router to it.
- `compose-farm.yaml` stays unchanged: just `hosts` and `stacks: stack → host`.

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

Re‑run this after changing Traefik labels, moving a stack to another host, or changing
published ports.

**Auto-regeneration**

To automatically regenerate the Traefik config after `up`, `down`, `restart`, or `update`,
add `traefik_file` to your config:

```yaml
compose_dir: /opt/compose
traefik_file: /opt/traefik/dynamic.d/compose-farm.yml  # auto-regenerate on up/down/restart/update
traefik_stack: traefik  # skip stacks on same host (docker provider handles them)

hosts:
  # ...
stacks:
  traefik: server-1  # Traefik runs here
  plex: server-2     # Stacks on other hosts get file-provider entries
  # ...
```

The `traefik_stack` option specifies which stack runs Traefik. Stacks on the same host
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

## Host Resource Monitoring (Glances)

The web UI can display real-time CPU, memory, and load stats for all configured hosts. This uses [Glances](https://nicolargo.github.io/glances/), a cross-platform system monitoring tool with a REST API.

**Setup**

1. Deploy a Glances stack that runs on all hosts:

```yaml
# glances/compose.yaml
name: glances
services:
  glances:
    image: nicolargo/glances:latest
    container_name: glances
    restart: unless-stopped
    pid: host
    ports:
      - "61208:61208"
    environment:
      - GLANCES_OPT=-w  # Enable web server mode
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

2. Add it to your config as a multi-host stack:

```yaml
# compose-farm.yaml
stacks:
  glances: all  # Runs on every host

glances_stack: glances  # Enables resource stats in web UI
```

3. Deploy: `cf up glances`

4. **(Docker web UI only)** If running the web UI in a Docker container, set `CF_LOCAL_HOST` to your local hostname in `.env`:

```bash
echo "CF_LOCAL_HOST=nas" >> .env  # Replace 'nas' with your local host name
```

This tells the web UI to reach the local Glances via container name instead of IP (required due to Docker network isolation).

The web UI dashboard will now show a "Host Resources" section with live stats from all hosts. Hosts where Glances is unreachable show an error indicator.

**Live Stats Page**

With Glances configured, a Live Stats page (`/live-stats`) shows all running containers across all hosts:

- **Columns**: Stack, Service, Host, Image, Status, Uptime, CPU, Memory, Net I/O
- **Features**: Sorting, filtering, live updates (no SSH required—uses Glances REST API)

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

**Docker Contexts** — You can use `docker context create remote ssh://...` and `docker compose --context remote up`. But it's manual: you must remember which host runs which stack, there's no global view, no parallel execution, and no auto-migration.

**Kubernetes / Docker Swarm** — Full orchestration that abstracts away the hardware. But they require cluster initialization, separate control planes, and often rewriting compose files. They introduce complexity (consensus, overlay networks) unnecessary for static "pet" servers.

**Ansible / Terraform** — Infrastructure-as-Code tools that can SSH in and deploy containers. But they're push-based configuration management, not interactive CLIs. Great for setting up state, clumsy for day-to-day operations like `cf logs -f` or quickly restarting a stack.

**Portainer / Coolify** — Web-based management UIs. But they're UI-first and often require agents on your servers. Compose Farm is CLI-first and agentless.

**Compose Farm is the middle ground:** a robust CLI that productizes the manual SSH pattern. You get the "cluster feel" (unified commands, state tracking) without the "cluster cost" (complexity, agents, control planes).

## License

MIT
