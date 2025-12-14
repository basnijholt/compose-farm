# Compose Farm Examples

This folder contains example Docker Compose services for testing Compose Farm locally.

## Quick Start

```bash
cd examples

# Check status of all services
compose-farm ps

# Pull images
compose-farm pull --all

# Start hello-world (runs and exits)
compose-farm up hello

# Start nginx (stays running)
compose-farm up nginx

# Check nginx is running
curl localhost:8080

# View logs
compose-farm logs nginx

# Stop nginx
compose-farm down nginx

# Update all (pull + restart)
compose-farm update --all
```

## Traefik Example

Start Traefik and a sample service with Traefik labels:

```bash
cd examples

# Start Traefik (reverse proxy with dashboard)
compose-farm up traefik

# Start whoami (test service with Traefik labels)
compose-farm up whoami

# Access the services
curl -H "Host: whoami.localhost" http://localhost    # whoami via Traefik
curl http://localhost:8081                            # Traefik dashboard
curl http://localhost:18082                           # whoami direct

# Generate Traefik file-provider config (for multi-host setups)
compose-farm traefik-file --all

# Stop everything
compose-farm down --all
```

The `whoami/docker-compose.yml` shows the standard Traefik label pattern:

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.whoami.rule=Host(`whoami.localhost`)
  - traefik.http.routers.whoami.entrypoints=web
  - traefik.http.services.whoami.loadbalancer.server.port=80
```

## Services

| Service | Description | Ports |
|---------|-------------|-------|
| hello | Hello-world container (exits immediately) | - |
| nginx | Nginx web server | 8080 |
| traefik | Traefik reverse proxy with dashboard | 80, 8081 |
| whoami | Test service with Traefik labels | 18082 |

## Config

The `compose-farm.yaml` in this directory configures all services to run locally (no SSH).
It also demonstrates the `traefik_file` option for auto-regenerating Traefik file-provider config.
