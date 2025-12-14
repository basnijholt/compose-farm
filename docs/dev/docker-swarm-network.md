# Docker Swarm Overlay Networks with Compose Farm

Notes from testing Docker Swarm's attachable overlay networks as a way to get cross-host container networking while still using `docker compose`.

## The Idea

Docker Swarm overlay networks can be made "attachable", allowing regular `docker compose` containers (not just swarm services) to join them. This would give us:

- Cross-host Docker DNS (containers find each other by name)
- No need to publish ports for inter-container communication
- Keep using `docker compose up` instead of `docker stack deploy`

## Setup Steps

```bash
# On manager node
docker swarm init --advertise-addr <manager-ip>

# On worker nodes (use token from init output)
docker swarm join --token <token> <manager-ip>:2377

# Create attachable overlay network (on manager)
docker network create --driver overlay --attachable my-network

# In compose files, add the network
networks:
  my-network:
    external: true
```

## Required Ports

Docker Swarm requires these ports open **bidirectionally** between all nodes:

| Port | Protocol | Purpose |
|------|----------|---------|
| 2377 | TCP | Cluster management |
| 7946 | TCP + UDP | Node communication |
| 4789 | UDP | Overlay network traffic (VXLAN) |

## Test Results (2024-12-13)

- docker-debian (192.168.1.66) as manager
- dev-lxc (192.168.1.167) as worker

### What worked

- Swarm init and join
- Overlay network creation
- Nodes showed as Ready

### What failed

- Container on dev-lxc couldn't attach to overlay network
- Error: `attaching to network failed... context deadline exceeded`
- Cause: Port 7946 blocked from docker-debian → dev-lxc

### Root cause

Firewall on dev-lxc wasn't configured to allow swarm ports. Opening these ports requires sudo access on each node.

## Conclusion

Docker Swarm overlay networks are **not plug-and-play**. Requirements:

1. Swarm init/join on all nodes
2. Firewall rules on all nodes (needs sudo/root)
3. All nodes must have bidirectional connectivity on 3 ports

For a simpler alternative, consider:

- **Tailscale**: VPN mesh, containers use host's Tailscale IP
- **Host networking + published ports**: What compose-farm does today
- **Keep dependent services together**: Avoid cross-host networking entirely

## Future Work

If we decide to support overlay networks:

1. Add a `compose-farm network create` command that:
   - Initializes swarm if needed
   - Creates attachable overlay network
   - Documents required firewall rules

2. Add network config to compose-farm.yaml:
   ```yaml
   overlay_network: compose-farm-net
   ```

3. Auto-inject network into compose files (or document manual setup)
