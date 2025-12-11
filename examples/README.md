# SDC Examples

This folder contains example Docker Compose services for testing SDC locally.

## Quick Start

```bash
cd examples

# Check status of all services
sdc ps

# Pull images
sdc pull --all

# Start hello-world (runs and exits)
sdc up hello

# Start nginx (stays running)
sdc up nginx

# Check nginx is running
curl localhost:8080

# View logs
sdc logs nginx

# Stop nginx
sdc down nginx

# Update all (pull + restart)
sdc update --all
```

## Services

- **hello**: Simple hello-world container (exits immediately)
- **nginx**: Nginx web server on port 8080

## Config

The `sdc.yaml` in this directory configures both services to run locally (no SSH).
