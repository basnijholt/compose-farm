# Compose Farm Full Example

A complete starter setup with Traefik reverse proxy and a test service.

## Quick Start

1. **Create the Docker network** (once per host):
   ```bash
   docker network create --subnet=172.20.0.0/16 --gateway=172.20.0.1 mynetwork
   ```

2. **Create data directory for Traefik**:
   ```bash
   mkdir -p /mnt/data/traefik
   ```

3. **Edit configuration**:
   - Update `compose-farm.yaml` with your host IP
   - Update `.env` files with your domain

4. **Start the stacks**:
   ```bash
   cf up traefik whoami
   ```

5. **Test**:
   - Dashboard: http://localhost:8080
   - Whoami: Add `whoami.example.com` to /etc/hosts pointing to your host

## Files

```
full/
├── compose-farm.yaml    # Compose Farm config
├── traefik/
│   ├── compose.yaml     # Traefik reverse proxy
│   └── .env
└── whoami/
    ├── compose.yaml     # Test HTTP service
    └── .env
```
