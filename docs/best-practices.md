---
icon: lucide/lightbulb
---

# Best Practices

Tips, limitations, and recommendations for using Compose Farm effectively.

## Limitations

### No Cross-Host Networking

Compose Farm moves containers between hosts but **does not provide cross-host networking**. Docker's internal DNS and networks don't span hosts.

**What breaks when you move a stack:**

| Feature | Works? | Why |
|---------|--------|-----|
| `http://redis:6379` | No | Docker DNS doesn't cross hosts |
| Docker network names | No | Networks are per-host |
| `DATABASE_URL=postgres://db:5432` | No | Container name won't resolve |
| Host IP addresses | Yes | Use `192.168.1.10:5432` |

### What Compose Farm Doesn't Do

- No overlay networking (use Swarm/Kubernetes)
- No service discovery across hosts
- No automatic dependency tracking between compose files
- No health checks or restart policies beyond Docker's
- No secrets management beyond Docker's

## Stack Organization

### Keep Dependencies Together

If services talk to each other, keep them in the same compose file on the same host:

```yaml
# /opt/compose/myapp/docker-compose.yml
services:
  app:
    image: myapp
    depends_on:
      - db
      - redis

  db:
    image: postgres

  redis:
    image: redis
```

```yaml
# compose-farm.yaml
stacks:
  myapp: nuc  # All three containers stay together
```

### Separate Standalone Stacks

Stacks whose services don't talk to other containers can be anywhere:

```yaml
stacks:
  # These can run on any host
  plex: nuc
  jellyfin: hp
  homeassistant: nas

  # These should stay together
  myapp: nuc  # includes app + db + redis
```

### Cross-Host Communication

If services MUST communicate across hosts, publish ports:

```yaml
# Instead of
DATABASE_URL=postgres://db:5432

# Use
DATABASE_URL=postgres://192.168.1.10:5432
```

```yaml
# And publish the port
services:
  db:
    ports:
      - "5432:5432"
```

## Multi-Host Stacks

### When to Use `all`

Use `all` for stacks that need local access to each host:

```yaml
stacks:
  # Need Docker socket
  dozzle: all          # Log viewer
  portainer-agent: all  # Portainer agents
  autokuma: all        # Auto-creates monitors

  # Need host metrics
  node-exporter: all   # Prometheus metrics
  promtail: all        # Log shipping
```

### Host-Specific Lists

For stacks on specific hosts only:

```yaml
stacks:
  # Only on compute nodes
  gitlab-runner: [nuc, hp]

  # Only on storage nodes
  minio: [nas-1, nas-2]
```

## Migration Safety

### Pre-flight Checks

Before migrating, Compose Farm verifies:
- Compose file is accessible on new host
- Required mounts exist on new host
- Required networks exist on new host

### Data Considerations

**Compose Farm doesn't move data.** Ensure:

1. **Shared storage**: Data volumes on NFS/shared storage
2. **External databases**: Data in external DB, not container
3. **Backup first**: Always backup before migration

### Safe Migration Pattern

```bash
# 1. Preview changes
cf apply --dry-run

# 2. Verify target host can run the stack
cf check myservice

# 3. Apply changes
cf apply
```

## State Management

### When to Refresh

Run `cf refresh` after:
- Manual `docker compose` commands
- Container restarts
- Host reboots
- Any changes outside Compose Farm

```bash
cf refresh --dry-run  # Preview
cf refresh            # Sync
```

### State Conflicts

If state doesn't match reality:

```bash
# See what's actually running
cf refresh --dry-run

# Sync state
cf refresh

# Then apply config
cf apply
```

## Shared Storage

### NFS Best Practices

```bash
# Mount options for Docker compatibility
nas:/compose /opt/compose nfs rw,hard,intr,rsize=8192,wsize=8192 0 0
```

### Directory Ownership

Ensure consistent UID/GID across hosts:

```yaml
services:
  myapp:
    environment:
      - PUID=1000
      - PGID=1000
```

### Config vs Data

Keep config and data separate:

```
/opt/compose/          # Shared: compose files + config
├── plex/
│   ├── docker-compose.yml
│   └── config/        # Small config files OK

/mnt/data/             # Shared: large media files
├── movies/
├── tv/
└── music/

/opt/appdata/          # Local: per-host app data
├── plex/
└── grafana/
```

## Performance

### Parallel Operations

Compose Farm runs operations in parallel. For large deployments:

```bash
# Good: parallel by default
cf up --all

# Avoid: sequential updates when possible
for svc in plex grafana nextcloud; do
  cf update $svc
done
```

### SSH Connection Reuse

SSH connections are reused within a command. For many operations:

```bash
# One command, one connection per host
cf update --all

# Multiple commands, multiple connections (slower)
cf update plex && cf update grafana && cf update nextcloud
```

## Traefik Setup

### Stack Placement

Put Traefik on a reliable host:

```yaml
stacks:
  traefik: nuc  # Primary host with good uptime
```

### Same-Host Stacks

Stacks on the same host as Traefik use Docker provider:

```yaml
traefik_stack: traefik

stacks:
  traefik: nuc
  portainer: nuc   # Docker provider handles this
  plex: hp         # File provider handles this
```

### Middleware in Separate File

Define middlewares outside Compose Farm's generated file:

```yaml
# /opt/traefik/dynamic.d/middlewares.yml
http:
  middlewares:
    redirect-https:
      redirectScheme:
        scheme: https
```

## Backup Strategy

### What to Backup

| Item | Location | Method |
|------|----------|--------|
| Compose Farm config | `~/.config/compose-farm/` | Git or copy |
| Compose files | `/opt/compose/` | Git |
| State file | `~/.config/compose-farm/compose-farm-state.yaml` | Optional (can refresh) |
| App data | `/opt/appdata/` | Backup solution |

### Disaster Recovery

```bash
# Restore config
cp backup/compose-farm.yaml ~/.config/compose-farm/

# Refresh state from running containers
cf refresh

# Or start fresh
cf apply
```

## Troubleshooting

### Common Issues

**Stack won't start:**
```bash
cf check myservice      # Verify mounts/networks
cf logs myservice       # Check container logs
```

**Migration fails:**
```bash
cf check myservice      # Verify new host is ready
cf init-network newhost # Create network if missing
```

**State out of sync:**
```bash
cf refresh --dry-run    # See differences
cf refresh              # Sync state
```

**SSH issues:**
```bash
cf ssh status           # Check key status
cf ssh setup            # Re-setup keys
```

## Security Considerations

### SSH Keys

- Use dedicated SSH key for Compose Farm
- Limit key to specific hosts if possible
- Don't store keys in Docker images

### Network Exposure

- Published ports are accessible from network
- Use firewalls for sensitive services
- Consider VPN for cross-host communication

### Secrets

- Don't commit `.env` files with secrets
- Use Docker secrets or external secret management
- Avoid secrets in compose file labels

## Comparison: When to Use Alternatives

| Scenario | Solution |
|----------|----------|
| 2-10 hosts, static stacks | **Compose Farm** |
| Cross-host container networking | Docker Swarm |
| Auto-scaling, self-healing | Kubernetes |
| Infrastructure as code | Ansible + Compose Farm |
| High availability requirements | Kubernetes or Swarm |
