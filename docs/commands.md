---
icon: lucide/terminal
---

# Commands Reference

The Compose Farm CLI is available as both `compose-farm` and the shorter alias `cf`.

## Command Overview

| Category | Command | Description |
|----------|---------|-------------|
| **Lifecycle** | `apply` | Make reality match config |
| | `up` | Start services |
| | `down` | Stop services |
| | `restart` | Restart services (down + up) |
| | `update` | Update services (pull + build + down + up) |
| | `pull` | Pull latest images |
| **Monitoring** | `ps` | Show service status |
| | `logs` | Show service logs |
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
| `--no-orphans` | Skip stopping orphaned services |
| `--full, -f` | Also refresh running services |
| `--config, -c PATH` | Path to config file |

**What it does:**

1. Stops orphaned services (in state but removed from config)
2. Migrates services on wrong host
3. Starts missing services (in config but not running)

**Examples:**

```bash
# Preview what would change
cf apply --dry-run

# Apply all changes
cf apply

# Only start/migrate, don't stop orphans
cf apply --no-orphans

# Also refresh all running services
cf apply --full
```

---

### cf up

Start services. Auto-migrates if host assignment changed.

```bash
cf up [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Start all services |
| `--host, -H TEXT` | Filter to services on this host |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Start specific services
cf up plex sonarr

# Start all services
cf up --all

# Start all services on a specific host
cf up --all --host nuc
```

**Auto-migration:**

If you change a service's host in config and run `cf up`:

1. Verifies mounts/networks exist on new host
2. Runs `down` on old host
3. Runs `up -d` on new host
4. Updates state

---

### cf down

Stop services.

```bash
cf down [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Stop all services |
| `--orphaned` | Stop orphaned services only |
| `--host, -H TEXT` | Filter to services on this host |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Stop specific services
cf down plex

# Stop all services
cf down --all

# Stop services removed from config
cf down --orphaned

# Stop all services on a host
cf down --all --host nuc
```

---

### cf restart

Restart services (down + up).

```bash
cf restart [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Restart all services |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
cf restart plex
cf restart --all
```

---

### cf update

Update services (pull + build + down + up).

<video autoplay loop muted playsinline>
  <source src="/assets/update.webm" type="video/webm">
</video>

```bash
cf update [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Update all services |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Update specific service
cf update plex

# Update all services
cf update --all
```

---

### cf pull

Pull latest images.

```bash
cf pull [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Pull for all services |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
cf pull plex
cf pull --all
```

---

## Monitoring Commands

### cf ps

Show status of services.

```bash
cf ps [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Show all services (default) |
| `--host, -H TEXT` | Filter to services on this host |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Show all services
cf ps

# Show specific services
cf ps plex sonarr

# Filter by host
cf ps --host nuc
```

---

### cf logs

Show service logs.

<video autoplay loop muted playsinline>
  <source src="/assets/logs.webm" type="video/webm">
</video>

```bash
cf logs [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Show logs for all services |
| `--host, -H TEXT` | Filter to services on this host |
| `--follow, -f` | Follow logs (live stream) |
| `--tail, -n INTEGER` | Number of lines (default: 20 for --all, 100 otherwise) |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Show last 100 lines
cf logs plex

# Follow logs
cf logs -f plex

# Show last 50 lines of multiple services
cf logs -n 50 plex sonarr

# Show last 20 lines of all services
cf logs --all
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
cf check [OPTIONS] [SERVICES]...
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

# Check specific service and show host compatibility
cf check jellyfin
```

---

### cf refresh

Update local state from running services.

```bash
cf refresh [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--dry-run, -n` | Show what would change |
| `--log-path, -l PATH` | Path to Dockerfarm TOML log |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Sync state with reality
cf refresh

# Preview changes
cf refresh --dry-run
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
cf traefik-file [OPTIONS] [SERVICES]...
```

**Options:**

| Option | Description |
|--------|-------------|
| `--all, -a` | Generate for all services |
| `--output, -o PATH` | Output file (stdout if omitted) |
| `--config, -c PATH` | Path to config file |

**Examples:**

```bash
# Preview to stdout
cf traefik-file --all

# Write to file
cf traefik-file --all -o /opt/traefik/dynamic.d/cf.yml

# Specific services
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

**Options for `cf ssh setup` and `cf ssh keygen`:**

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

# Update a specific service
cf update plex

# View logs
cf logs -f plex
```

### Maintenance

```bash
# Update all services
cf update --all

# Refresh state after manual changes
cf refresh
```

### Migration

```bash
# Preview what would change
cf apply --dry-run

# Move a service: edit config, then
cf up plex  # auto-migrates

# Or reconcile everything
cf apply
```

### Troubleshooting

```bash
# Validate config
cf check --local
cf check

# Check specific service
cf check jellyfin

# Sync state
cf refresh --dry-run
cf refresh
```
