# Title options

- Multi-host Docker Compose without Kubernetes or file changes
- I built a CLI to run Docker Compose across hosts. Zero changes to your files.

---

# I made a CLI to run Docker Compose across multiple hosts without Kubernetes or Swarm

I've been running 100+ Docker Compose stacks on a single machine, and it kept running out of memory. I needed to spread services across multiple hosts, but:

- **Kubernetes** felt like overkill. I don't need pods, ingress controllers, or 10x more YAML.
- **Docker Swarm** is basically in maintenance mode.
- Both require rewriting my compose files.

So I built **Compose Farm**, a simple CLI that runs `docker compose` commands over SSH. No agents, no cluster setup, no changes to your existing compose files.

## How it works

One YAML file maps services to hosts:

```yaml
compose_dir: /opt/stacks

hosts:
  nuc: 192.168.1.10
  hp: 192.168.1.11

services:
  plex: nuc
  jellyfin: hp
  sonarr: nuc
  radarr: nuc
```

Then just:

```bash
cf up plex        # runs on nuc via SSH
cf up --all       # starts everything on their assigned hosts
cf logs -f plex   # streams logs
cf ps             # shows status across all hosts
```

## Auto-migration

Change a service's host in the config and run `cf up`. It stops the service on the old host and starts it on the new one. No manual SSH needed.

```yaml
# Before
plex: nuc

# After (just change this)
plex: hp
```

```bash
cf up plex  # migrates automatically
```

## Requirements

- SSH key auth to your hosts
- Same paths on all hosts (I use NFS from my NAS)
- That's it. No agents, no daemons.

## What it doesn't do

- No high availability (if a host goes down, services don't auto-migrate)
- No overlay networking (containers on different hosts can't talk via Docker DNS)
- No service discovery
- No health checks or automatic restarts

It's a convenience wrapper around `docker compose` + SSH. If you need failover or cross-host container networking, you probably do need Swarm or Kubernetes.

## Links

- GitHub: https://github.com/basnijholt/compose-farm
- Install: `uv tool install compose-farm` or `pip install compose-farm`

Built this in 4 days because I was mass-SSHing into machines like a caveman. Happy to answer questions or take feedback!
