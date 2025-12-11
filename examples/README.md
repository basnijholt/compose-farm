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

## Services

- **hello**: Simple hello-world container (exits immediately)
- **nginx**: Nginx web server on port 8080

## Config

The `compose-farm.yaml` in this directory configures both services to run locally (no SSH).
