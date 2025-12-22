---
icon: lucide/terminal
---

# Commands Reference

The Compose Farm CLI is available as both `compose-farm` and the shorter alias `cf`.

## Command Overview

| Category | Command | Description |
|----------|---------|-------------|
| **Lifecycle** | `apply` | Make reality match config |
| | `up` | Start stacks |
| | `down` | Stop stacks |
| | `stop` | Stop services without removing containers |
| | `restart` | Restart stacks (down + up) |
| | `update` | Update stacks (pull + build + down + up) |
| | `pull` | Pull latest images |
| | `compose` | Run any docker compose command |
| **Monitoring** | `ps` | Show stack status |
| | `logs` | Show stack logs |
| | `stats` | Show overview statistics |
| **Configuration** | `check` | Validate config and mounts |
| | `refresh` | Sync state from reality |
| | `init-network` | Create Docker network |
| | `traefik-file` | Generate Traefik config |
| | `config` | Manage config files |
| | `ssh` | Manage SSH keys |
| **Server** | `web` | Start web UI |

## Global Options

```bash
cf --version, -v    # Show version
cf --help, -h       # Show help
```

---

## Lifecycle Commands

### cf apply

Make reality match your configuration. The primary reconciliation command.

<video autoplay loop muted playsinline>
  <source src="/assets/apply.webm" type="video/webm">
</video>

```bash
cf apply [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--dry-run, -n` | Preview changes without executing |
| `--no-orphans` | Skip stopping orphaned stacks |
| `--full, -f` | Also refresh running stacks |
| `--config, -c PATH` | Path to config file |

**What it does:**

1. Stops orphaned stacks (in state but removed from config)
2. Migrates stacks on wrong host
3. Starts missing stacks (in config but not running)

**Examples:**

```bash
# Preview what would change
cf apply --dry-run

# Apply all changes
cf apply

# Only start/migrate, don't stop orphans
cf apply --no-orphans

# Also refresh all running stacks
cf apply --full
```

---

### cf up

Start stacks. Auto-migrates if host assignment changed.

```bash
cf up [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Start all stacks |
| `--host, -H TEXT` | Filter to stacks on this host |
| `--service, -s TEXT` | Target a specific service within the stack |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Start specific stacks
cf up plex grafana

# Start all stacks
cf up --all

# Start all stacks on a specific host
cf up --all --host nuc

# Start a specific service within a stack
cf up immich --service database
```

**Auto-migration:**

If you change a stack's host in config and run `cf up`:

1. Verifies mounts/networks exist on new host
2. Runs `down` on old host
3. Runs `up -d` on new host
4. Updates state

---

### cf down

Stop stacks.

```bash
cf down [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Stop all stacks |
| `--orphaned` | Stop orphaned stacks only |
| `--host, -H TEXT` | Filter to stacks on this host |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Stop specific stacks
cf down plex

# Stop all stacks
cf down --all

# Stop stacks removed from config
cf down --orphaned

# Stop all stacks on a host
cf down --all --host nuc
```

---

### cf stop

Stop services without removing containers.

```bash
cf stop [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Stop all stacks |
| `--service, -s TEXT` | Target a specific service within the stack |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Stop specific stacks
cf stop plex

# Stop all stacks
cf stop --all

# Stop a specific service within a stack
cf stop immich --service database
```

---

### cf restart

Restart stacks (down + up). With `--service`, restarts just that service.

```bash
cf restart [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Restart all stacks |
| `--service, -s TEXT` | Target a specific service within the stack |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
cf restart plex
cf restart --all

# Restart a specific service
cf restart immich --service database
```

---

### cf update

Update stacks (pull + build + down + up). With `--service`, updates just that service.

<video autoplay loop muted playsinline>
  <source src="/assets/update.webm" type="video/webm">
</video>

```bash
cf update [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Update all stacks |
| `--service, -s TEXT` | Target a specific service within the stack |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Update specific stack
cf update plex

# Update all stacks
cf update --all

# Update a specific service
cf update immich --service database
```

---

### cf pull

Pull latest images.

```bash
cf pull [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Pull for all stacks |
| `--service, -s TEXT` | Target a specific service within the stack |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
cf pull plex
cf pull --all

# Pull a specific service
cf pull immich --service database
```

---

### cf compose

Run any docker compose command on a stack. This is a passthrough to docker compose for commands not wrapped by cf.

<video autoplay loop muted playsinline>
  <source src="/assets/compose.webm" type="video/webm">
</video>

```bash
cf compose [OPTIONS] STACK COMMAND [ARGS]...
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `STACK` | Stack to operate on (use `.` for current dir) |
| `COMMAND` | Docker compose command to run |
| `ARGS` | Additional arguments passed to docker compose |

**Options:**

| Option | Description |
|--------|-------------|
| `--host, -H TEXT` | Filter to stacks on this host (required for multi-host stacks) |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Show docker compose help
cf compose mystack --help

# View running processes
cf compose mystack top

# List images
cf compose mystack images

# Interactive shell
cf compose mystack exec web bash

# View parsed config
cf compose mystack config

# Use current directory as stack
cf compose . ps
```

---

## Monitoring Commands

### cf ps

Show status of stacks.

```bash
cf ps [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Show all stacks (default) |
| `--host, -H TEXT` | Filter to stacks on this host |
| `--service, -s TEXT` | Target a specific service within the stack |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Show all stacks
cf ps

# Show specific stacks
cf ps plex grafana

# Filter by host
cf ps --host nuc

# Show status of a specific service
cf ps immich --service database
```

---

### cf logs

Show stack logs.

<video autoplay loop muted playsinline>
  <source src="/assets/logs.webm" type="video/webm">
</video>

```bash
cf logs [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Show logs for all stacks |
| `--host, -H TEXT` | Filter to stacks on this host |
| `--service, -s TEXT` | Target a specific service within the stack |
| `--follow, -f` | Follow logs (live stream) |
| `--tail, -n INTEGER` | Number of lines (default: 20 for --all, 100 otherwise) |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Show last 100 lines
cf logs plex

# Follow logs
cf logs -f plex

# Show last 50 lines of multiple stacks
cf logs -n 50 plex grafana

# Show last 20 lines of all stacks
cf logs --all

# Show logs for a specific service
cf logs immich --service database
```

---

### cf stats

Show overview statistics.

```bash
cf stats [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--live, -l` | Query Docker for live container counts |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Config/state overview
cf stats

# Include live container counts
cf stats --live
```

---

## Configuration Commands

### cf check

Validate configuration, mounts, and networks.

```bash
cf check [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--local` | Skip SSH-based checks (faster) |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Full validation with SSH
cf check

# Fast local-only validation
cf check --local

# Check specific stack and show host compatibility
cf check jellyfin
```

---

### cf refresh

Update local state from running stacks.

```bash
cf refresh [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Refresh all stacks |
| `--dry-run, -n` | Show what would change |
| `--log-path, -l PATH` | Path to Dockerfarm TOML log |
| `--config, -c PATH` | Path to config file |

Without arguments, refreshes all stacks (same as `--all`). With stack names, refreshes only those stacks.

**Examples:**

```bash
# Sync state with reality (all stacks)
cf refresh

# Preview changes
cf refresh --dry-run

# Refresh specific stacks only
cf refresh plex sonarr
```

---

### cf init-network

Create Docker network on hosts with consistent settings.

```bash
cf init-network [OPTIONS] [HOSTS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--network, -n TEXT` | Network name (default: mynetwork) |
| `--subnet, -s TEXT` | Network subnet (default: 172.20.0.0/16) |
| `--gateway, -g TEXT` | Network gateway (default: 172.20.0.1) |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Create on all hosts
cf init-network

# Create on specific hosts
cf init-network nuc hp

# Custom network settings
cf init-network -n production -s 10.0.0.0/16 -g 10.0.0.1
```

---

### cf traefik-file

Generate Traefik file-provider config from compose labels.

```bash
cf traefik-file [OPTIONS] [STACKS]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Generate for all stacks |
| `--output, -o PATH` | Output file (stdout if omitted) |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Preview to stdout
cf traefik-file --all

# Write to file
cf traefik-file --all -o /opt/traefik/dynamic.d/cf.yml

# Specific stacks
cf traefik-file plex jellyfin -o /opt/traefik/cf.yml
```

---

### cf config

Manage configuration files.

```bash
cf config COMMAND
```

**Subcommands:**

| Command | Description |
|---------|-------------|
| `init` | Create new config with examples |
| `show` | Display config with highlighting |
| `path` | Print config file path |
| `validate` | Validate syntax and schema |
| `edit` | Open in $EDITOR |
| `symlink` | Create symlink from default location |

**Options by subcommand:**

| Subcommand | Options |
|------------|---------|
| `init` | `--path/-p PATH`, `--force/-f` |
| `show` | `--path/-p PATH`, `--raw/-r` |
| `edit` | `--path/-p PATH` |
| `path` | `--path/-p PATH` |
| `validate` | `--path/-p PATH` |
| `symlink` | `--force/-f` |

**Examples:**

```bash
# Create config at default location
cf config init

# Create config at custom path
cf config init --path /opt/compose-farm/config.yaml

# Show config with syntax highlighting
cf config show

# Show raw config (for copy-paste)
cf config show --raw

# Validate config
cf config validate

# Edit config in $EDITOR
cf config edit

# Print config path
cf config path

# Create symlink to local config
cf config symlink

# Create symlink to specific file
cf config symlink /opt/compose-farm/config.yaml
```

---

### cf ssh

Manage SSH keys for passwordless authentication.

```bash
cf ssh COMMAND
```

**Subcommands:**

| Command | Description |
|---------|-------------|
| `setup` | Generate key and copy to all hosts |
| `status` | Show SSH key status and host connectivity |
| `keygen` | Generate key without distributing |

**Options for `cf ssh setup`:**

| Option | Description |
|--------|-------------|
| `--config, -c PATH` | Path to config file |
| `--force, -f` | Regenerate key even if it exists |

**Options for `cf ssh status`:**

| Option | Description |
|--------|-------------|
| `--config, -c PATH` | Path to config file |

**Options for `cf ssh keygen`:**

| Option | Description |
|--------|-------------|
| `--force, -f` | Regenerate key even if it exists |

**Examples:**

```bash
# Set up SSH keys (generates and distributes)
cf ssh setup

# Check status and connectivity
cf ssh status

# Generate key only (don't distribute)
cf ssh keygen
```

---

## Server Commands

### cf web

Start the web UI server.

```bash
cf web [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--host, -H TEXT` | Host to bind to (default: 0.0.0.0) |
| `--port, -p INTEGER` | Port to listen on (default: 8000) |
| `--reload, -r` | Enable auto-reload for development |

**Note:** Requires web dependencies: `pip install compose-farm[web]`

**Examples:**

```bash
# Start on default port
cf web

# Start on custom port
cf web --port 3000

# Development mode with auto-reload
cf web --reload
```

---

## Common Patterns

### Daily Operations

```bash
# Morning: check status
cf ps
cf stats --live

# Update a specific stack
cf update plex

# View logs
cf logs -f plex
```

### Maintenance

```bash
# Update all stacks
cf update --all

# Refresh state after manual changes
cf refresh
```

### Migration

```bash
# Preview what would change
cf apply --dry-run

# Move a stack: edit config, then
cf up plex  # auto-migrates

# Or reconcile everything
cf apply
```

### Troubleshooting

```bash
# Validate config
cf check --local
cf check

# Check specific stack
cf check jellyfin

# Sync state
cf refresh --dry-run
cf refresh
```
