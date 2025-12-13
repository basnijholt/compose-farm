# Compose Farm – Traefik Multihost Ingress Plan

## Goal
Generate a Traefik file-provider fragment from existing docker-compose Traefik labels (no config duplication) so a single front-door Traefik on 192.168.1.66 with wildcard `*.lab.mydomain.org` can route to services running on other hosts. Keep the current simplicity (SSH + docker compose); no Swarm/K8s.

## Requirements
- Traefik stays on main host; keep current `dynamic.yml` and Docker provider for local containers.
- Add a watched directory provider (any path works) and load a generated fragment (e.g., `compose-farm.generated.yml`).
- No edits to compose files: reuse existing `traefik.*` labels as the single source of truth; Compose Farm only reads them.
- Generator infers routing from labels and reachability from `ports:` mappings; prefer host-published ports so Traefik can reach services across hosts. Upstreams point to `<host address>:<published host port>`; warn if no published port is found.
- Only minimal data in `compose-farm.yaml`: hosts map and service→host mapping (already present).
- No new orchestration/discovery layers; respect KISS/YAGNI/DRY.

## Non-Goals
- No Swarm/Kubernetes adoption.
- No global Docker provider across hosts.
- No health checks/service discovery layer.

## Current State (Dec 2025)
- Compose Farm: Typer CLI wrapping `docker compose` over SSH; config in `compose-farm.yaml`; parallel by default; snapshot/log tooling present.
- Traefik: single instance on 192.168.1.66, wildcard `*.lab.mydomain.org`, Docker provider for local services, file provider via `dynamic.yml` already in use.

## Proposed Implementation Steps
1) Add generator command: `compose-farm traefik-file --output <path>`.
2) Resolve per-service host from `compose-farm.yaml`; read compose file at `{compose_dir}/{service}/docker-compose.yml`.
3) Parse `traefik.*` labels to build routers/services/middlewares as in compose; map container port to published host port (from `ports:`) to form upstream URLs with host address.
4) Emit file-provider YAML to the watched directory (recommended default: `/mnt/data/traefik/dynamic.d/compose-farm.generated.yml`, but user chooses via `--output`).
5) Warnings: if no published port is found, warn that cross-host reachability requires L3 reachability to container IPs.
6) Tests: label parsing, port mapping, YAML render; scenario with published port; scenario without published port.
7) Docs: update README/CLAUDE to describe directory provider flags and the generator workflow; note that compose files remain unchanged.

## Open Questions
- How to derive target host address: use `hosts.<name>.address` verbatim, or allow override per service? (Default: use host address.)
- Should we support multiple hosts/backends per service for LB/HA? (Start with single server.)
- Where to store generated file by default? (Default to user-specified `--output`; maybe fallback to `./compose-farm-traefik.yml`.)
