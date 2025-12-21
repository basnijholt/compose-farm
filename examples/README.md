# Compose Farm Examples

Real-world examples demonstrating compose-farm patterns for multi-host Docker deployments.

## Stacks

| Stack | Type | Demonstrates |
|---------|------|--------------|
| [traefik](traefik/) | Infrastructure | Reverse proxy, Let's Encrypt, file-provider |
| [mealie](mealie/) | Single container | Traefik labels, resource limits, environment vars |
| [uptime-kuma](uptime-kuma/) | Single container | Docker socket, user mapping, custom DNS |
| [paperless-ngx](paperless-ngx/) | Multi-container | Redis + App stack (SQLite) |
| [autokuma](autokuma/) | Multi-host | Demonstrates `all` keyword (runs on every host) |

## Key Patterns

### External Network

All stacks connect to a shared external network for inter-service communication:

```yaml
networks:
  mynetwork:
    external: true
```

Create it on each host with consistent settings:

```bash
compose-farm init-network --network mynetwork --subnet 172.20.0.0/16
```

### Traefik Labels (Dual Routes)

Stacks expose two routes for different access patterns:

1. **HTTPS route** (`websecure` entrypoint): For your custom domain with Let's Encrypt TLS
2. **HTTP route** (`web` entrypoint): For `.local` domains on your LAN (no TLS needed)

This pattern allows accessing stacks via:
- `https://mealie.example.com` - from anywhere, with TLS
- `http://mealie.local` - from your local network, no TLS overhead

```yaml
labels:
  # HTTPS route for custom domain (e.g., mealie.example.com)
  - traefik.enable=true
  - traefik.http.routers.myapp.rule=Host(`myapp.${DOMAIN}`)
  - traefik.http.routers.myapp.entrypoints=websecure
  - traefik.http.services.myapp.loadbalancer.server.port=8080
  # HTTP route for .local domain (e.g., myapp.local)
  - traefik.http.routers.myapp-local.rule=Host(`myapp.local`)
  - traefik.http.routers.myapp-local.entrypoints=web
```

> **Note:** `.local` domains require local DNS (e.g., Pi-hole, Technitium) to resolve to your Traefik host.

### Environment Variables

Each stack has a `.env` file for secrets and domain configuration.
Edit these files to set your domain and credentials:

```bash
# Example: set your domain
echo "DOMAIN=example.com" > mealie/.env
```

Variables like `${DOMAIN}` are substituted at runtime by Docker Compose.

### NFS Volume Mounts

All data is stored on shared NFS storage at `/mnt/data/`:

```yaml
volumes:
  - /mnt/data/myapp:/app/data
```

This allows stacks to migrate between hosts without data loss.

### Multi-Host Stacks

Stacks that need to run on every host (e.g., monitoring agents):

```yaml
# In compose-farm.yaml
stacks:
  autokuma: all  # Runs on every configured host
```

### Multi-Container Stacks

Database-backed apps with multiple services:

```yaml
services:
  redis:
    image: redis:7
  app:
    depends_on:
      - redis
```

> **NFS + PostgreSQL Warning:** PostgreSQL should NOT run on NFS storage due to
> fsync and file locking issues. Use SQLite (safe for single-writer on NFS) or
> keep PostgreSQL data on local volumes (non-migratable).

### AutoKuma Labels (Optional)

The autokuma example demonstrates compose-farm's **multi-host feature** - running the same stack on all hosts using the `all` keyword. AutoKuma itself is not part of compose-farm; it's just a good example because it needs to run on every host to monitor local Docker containers.

[AutoKuma](https://github.com/BigBoot/AutoKuma) automatically creates Uptime Kuma monitors from Docker labels:

```yaml
labels:
  - kuma.myapp.http.name=My App
  - kuma.myapp.http.url=https://myapp.${DOMAIN}
```

## Quick Start

```bash
cd examples

# 1. Create the shared network on all hosts
compose-farm init-network

# 2. Start Traefik first (the reverse proxy)
compose-farm up traefik

# 3. Start other stacks
compose-farm up mealie uptime-kuma

# 4. Check status
compose-farm ps

# 5. Generate Traefik file-provider config for cross-host routing
compose-farm traefik-file --all

# 6. View logs
compose-farm logs mealie

# 7. Stop everything
compose-farm down --all
```

## Configuration

The `compose-farm.yaml` shows a multi-host setup:

- **primary** (192.168.1.10): Runs Traefik and heavy stacks
- **secondary** (192.168.1.11): Runs lighter stacks
- **autokuma**: Runs on ALL hosts to monitor local containers

When Traefik runs on `primary` and a stack runs on `secondary`, compose-farm
automatically generates file-provider config so Traefik can route to it.

## Traefik File-Provider

When stacks run on different hosts than Traefik, use `traefik-file` to generate routing config:

```bash
# Generate config for all stacks
compose-farm traefik-file --all -o traefik/dynamic.d/compose-farm.yml

# Or configure auto-generation in compose-farm.yaml:
traefik_file: /opt/stacks/traefik/dynamic.d/compose-farm.yml
traefik_stack: traefik
```

With `traefik_file` configured, compose-farm automatically regenerates the config after `up`, `down`, `restart`, and `update` commands.
