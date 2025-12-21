---
icon: lucide/settings
---

# Configuration Reference

Compose Farm uses a YAML configuration file to define hosts and stack assignments.

## Config File Location

Compose Farm looks for configuration in this order:

1. `-c` / `--config` flag (if provided)
2. `CF_CONFIG` environment variable
3. `./compose-farm.yaml` (current directory)
4. `$XDG_CONFIG_HOME/compose-farm/compose-farm.yaml` (defaults to `~/.config`)

Use `-c` / `--config` to specify a custom path:

```bash
cf ps -c /path/to/config.yaml
```

Or set the environment variable:

```bash
export CF_CONFIG=/path/to/config.yaml
```

## Examples

### Single host (local-only)

```yaml
# Required: directory containing compose files
compose_dir: /opt/stacks

# Define local host
hosts:
  local: localhost

# Map stacks to the local host
stacks:
  plex: local
  grafana: local
  nextcloud: local
```

### Multi-host (full example)

```yaml
# Required: directory containing compose files (same path on all hosts)
compose_dir: /opt/compose

# Optional: auto-regenerate Traefik config
traefik_file: /opt/traefik/dynamic.d/compose-farm.yml
traefik_stack: traefik

# Define Docker hosts
hosts:
  nuc:
    address: 192.168.1.10
    user: docker
  hp:
    address: 192.168.1.11
    user: admin

# Map stacks to hosts
stacks:
  # Single-host stacks
  plex: nuc
  grafana: nuc
  nextcloud: hp

  # Multi-host stacks
  dozzle: all                    # Run on ALL hosts
  node-exporter: [nuc, hp]       # Run on specific hosts
```

## Settings Reference

### compose_dir (required)

Directory containing your compose stack folders. Must be the same path on all hosts.

```yaml
compose_dir: /opt/compose
```

**Directory structure:**

```
/opt/compose/
├── plex/
│   ├── docker-compose.yml    # or compose.yaml
│   └── .env                  # optional environment file
├── grafana/
│   └── docker-compose.yml
└── ...
```

Supported compose file names (checked in order):
- `compose.yaml`
- `compose.yml`
- `docker-compose.yml`
- `docker-compose.yaml`

### traefik_file

Path to auto-generated Traefik file-provider config. When set, Compose Farm regenerates this file after `up`, `down`, `restart`, and `update` commands.

```yaml
traefik_file: /opt/traefik/dynamic.d/compose-farm.yml
```

### traefik_stack

Stack name running Traefik. Stacks on the same host are skipped in file-provider config (Traefik's docker provider handles them).

```yaml
traefik_stack: traefik
```

## Hosts Configuration

### Basic Host

```yaml
hosts:
  myserver:
    address: 192.168.1.10
```

### With SSH User

```yaml
hosts:
  myserver:
    address: 192.168.1.10
    user: docker
```

If `user` is omitted, the current user is used.

### With Custom SSH Port

```yaml
hosts:
  myserver:
    address: 192.168.1.10
    user: docker
    port: 2222  # SSH port (default: 22)
```

### Localhost

For stacks running on the same machine where you invoke Compose Farm:

```yaml
hosts:
  local: localhost
```

No SSH is used for localhost stacks.

### Multiple Hosts

```yaml
hosts:
  nuc:
    address: 192.168.1.10
    user: docker
  hp:
    address: 192.168.1.11
    user: admin
  truenas:
    address: 192.168.1.100
  local: localhost
```

## Stacks Configuration

### Single-Host Stack

```yaml
stacks:
  plex: nuc
  grafana: nuc
  nextcloud: hp
```

### Multi-Host Stack

For stacks that need to run on every host (e.g., log shippers, monitoring agents):

```yaml
stacks:
  # Run on ALL configured hosts
  dozzle: all
  promtail: all

  # Run on specific hosts
  node-exporter: [nuc, hp, truenas]
```

**Common multi-host stacks:**
- **Dozzle** - Docker log viewer (needs local socket)
- **Promtail/Alloy** - Log shipping (needs local socket)
- **node-exporter** - Host metrics (needs /proc, /sys)
- **AutoKuma** - Uptime Kuma monitors (needs local socket)

### Stack Names

Stack names must match directory names in `compose_dir`:

```yaml
compose_dir: /opt/compose
stacks:
  plex: nuc      # expects /opt/compose/plex/docker-compose.yml
  my-app: hp     # expects /opt/compose/my-app/docker-compose.yml
```

## State File

Compose Farm tracks deployment state in `compose-farm-state.yaml`, stored alongside the config file.

For example, if your config is at `~/.config/compose-farm/compose-farm.yaml`, the state file will be at `~/.config/compose-farm/compose-farm-state.yaml`.

```yaml
deployed:
  plex: nuc
  grafana: nuc
```

This file records which stacks are deployed and on which host.

**Don't edit manually.** Use `cf refresh` to sync state with reality.

## Environment Variables

### In Compose Files

Your compose files can use `.env` files as usual:

```
/opt/compose/plex/
├── docker-compose.yml
└── .env
```

Compose Farm runs `docker compose` which handles `.env` automatically.

### In Traefik Labels

When generating Traefik config, Compose Farm resolves `${VAR}` and `${VAR:-default}` from:

1. The stack's `.env` file
2. Current environment

## Config Commands

### Initialize Config

```bash
cf config init
```

Creates a new config file with documented examples.

### Validate Config

```bash
cf config validate
```

Checks syntax and schema.

### Show Config

```bash
cf config show
```

Displays current config with syntax highlighting.

### Edit Config

```bash
cf config edit
```

Opens config in `$EDITOR`.

### Show Config Path

```bash
cf config path
```

Prints the config file location (useful for scripting).

### Create Symlink

```bash
cf config symlink                          # Link to ./compose-farm.yaml
cf config symlink /path/to/my-config.yaml  # Link to specific file
```

Creates a symlink from the default location (`~/.config/compose-farm/compose-farm.yaml`) to your config file. Use `--force` to overwrite an existing symlink.

## Validation

### Local Validation

Fast validation without SSH:

```bash
cf check --local
```

Checks:
- Config syntax
- Stack-to-host mappings
- Compose file existence

### Full Validation

```bash
cf check
```

Additional SSH-based checks:
- Host connectivity
- Mount point existence
- Docker network existence
- Traefik label validation

### Stack-Specific Check

```bash
cf check jellyfin
```

Shows which hosts can run the stack (have required mounts/networks).

## Example Configurations

### Minimal

```yaml
compose_dir: /opt/compose

hosts:
  server: 192.168.1.10

stacks:
  myapp: server
```

### Home Lab

```yaml
compose_dir: /opt/compose

hosts:
  nuc:
    address: 192.168.1.10
    user: docker
  nas:
    address: 192.168.1.100
    user: admin

stacks:
  # Media
  plex: nuc
  jellyfin: nuc
  immich: nuc

  # Infrastructure
  traefik: nuc
  portainer: nuc

  # Monitoring (on all hosts)
  dozzle: all
```

### Production

```yaml
compose_dir: /opt/compose
traefik_file: /opt/traefik/dynamic.d/cf.yml
traefik_stack: traefik

hosts:
  web-1:
    address: 10.0.1.10
    user: deploy
  web-2:
    address: 10.0.1.11
    user: deploy
  db:
    address: 10.0.1.20
    user: deploy

stacks:
  # Load balanced
  api: [web-1, web-2]

  # Single instance
  postgres: db
  redis: db

  # Infrastructure
  traefik: web-1

  # Monitoring
  promtail: all
```
