"""Glances API client for host resource monitoring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config

# Default Glances REST API port
DEFAULT_GLANCES_PORT = 61208


@dataclass
class HostStats:
    """Resource statistics for a host."""

    host: str
    cpu_percent: float
    mem_percent: float
    swap_percent: float
    load: float
    disk_percent: float
    error: str | None = None

    @classmethod
    def from_error(cls, host: str, error: str) -> HostStats:
        """Create a HostStats with an error."""
        return cls(
            host=host,
            cpu_percent=0,
            mem_percent=0,
            swap_percent=0,
            load=0,
            disk_percent=0,
            error=error,
        )


async def fetch_host_stats(
    host_name: str,
    host_address: str,
    port: int = DEFAULT_GLANCES_PORT,
    request_timeout: float = 5.0,
) -> HostStats:
    """Fetch stats from a single host's Glances API."""
    import httpx  # noqa: PLC0415

    base_url = f"http://{host_address}:{port}/api/4"

    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            # Fetch quicklook stats (CPU, mem, load)
            response = await client.get(f"{base_url}/quicklook")
            if not response.is_success:
                return HostStats.from_error(host_name, f"HTTP {response.status_code}")
            data = response.json()

            # Fetch filesystem stats for disk usage
            disk_percent = 0.0
            try:
                fs_response = await client.get(f"{base_url}/fs")
                if fs_response.is_success:
                    fs_data = fs_response.json()
                    # Find root filesystem or use max usage across all filesystems
                    for fs in fs_data:
                        if fs.get("mnt_point") == "/":
                            disk_percent = fs.get("percent", 0)
                            break
                    else:
                        # No root found, use highest usage
                        if fs_data:
                            disk_percent = max(fs.get("percent", 0) for fs in fs_data)
            except httpx.HTTPError:
                pass  # Disk stats are optional

            return HostStats(
                host=host_name,
                cpu_percent=data.get("cpu", 0),
                mem_percent=data.get("mem", 0),
                swap_percent=data.get("swap", 0),
                load=data.get("load", 0),
                disk_percent=disk_percent,
            )
    except httpx.TimeoutException:
        return HostStats.from_error(host_name, "timeout")
    except httpx.HTTPError as e:
        return HostStats.from_error(host_name, str(e))
    except Exception as e:
        return HostStats.from_error(host_name, str(e))


async def fetch_all_host_stats(
    config: Config,
    port: int = DEFAULT_GLANCES_PORT,
) -> dict[str, HostStats]:
    """Fetch stats from all hosts in parallel."""
    tasks = [fetch_host_stats(name, host.address, port) for name, host in config.hosts.items()]
    results = await asyncio.gather(*tasks)
    return {stats.host: stats for stats in results}


@dataclass
class ContainerStats:
    """Container statistics from Glances."""

    name: str
    host: str
    status: str
    image: str
    cpu_percent: float
    memory_usage: int  # bytes
    memory_limit: int  # bytes
    memory_percent: float
    network_rx: int  # cumulative bytes received
    network_tx: int  # cumulative bytes sent
    uptime: str
    ports: str
    engine: str  # docker, podman, etc.
    stack: str = ""  # compose project name (from docker labels)
    service: str = ""  # compose service name (from docker labels)

    @property
    def memory_usage_mb(self) -> float:
        """Memory usage in MB."""
        return self.memory_usage / (1024 * 1024)

    @property
    def memory_limit_mb(self) -> float:
        """Memory limit in MB."""
        return self.memory_limit / (1024 * 1024)


def _parse_container(data: dict[str, Any], host_name: str) -> ContainerStats:
    """Parse container data from Glances API response."""
    # Image can be a list or string
    image = data.get("image", ["unknown"])
    if isinstance(image, list):
        image = image[0] if image else "unknown"

    # Calculate memory percent
    mem_usage = data.get("memory_usage", 0) or 0
    mem_limit = data.get("memory_limit", 1) or 1  # Avoid division by zero
    mem_percent = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0

    # Network stats
    network = data.get("network", {}) or {}
    network_rx = network.get("cumulative_rx", 0) or 0
    network_tx = network.get("cumulative_tx", 0) or 0

    return ContainerStats(
        name=data.get("name", "unknown"),
        host=host_name,
        status=data.get("status", "unknown"),
        image=image,
        cpu_percent=data.get("cpu_percent", 0) or 0,
        memory_usage=mem_usage,
        memory_limit=mem_limit,
        memory_percent=mem_percent,
        network_rx=network_rx,
        network_tx=network_tx,
        uptime=data.get("uptime", ""),
        ports=data.get("ports", "") or "",
        engine=data.get("engine", "docker"),
    )


async def fetch_container_stats(
    host_name: str,
    host_address: str,
    port: int = DEFAULT_GLANCES_PORT,
    request_timeout: float = 5.0,
) -> list[ContainerStats]:
    """Fetch container stats from a single host's Glances API."""
    import httpx  # noqa: PLC0415

    url = f"http://{host_address}:{port}/api/4/containers"

    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            response = await client.get(url)
            if not response.is_success:
                return []
            data = response.json()
            return [_parse_container(c, host_name) for c in data]
    except (httpx.TimeoutException, httpx.HTTPError, Exception):
        return []


async def _fetch_compose_labels(
    config: Config,
    host_name: str,
) -> dict[str, tuple[str, str]]:
    """Fetch compose labels for all containers on a host.

    Returns dict of container_name -> (stack, service).
    """
    from .executor import run_command  # noqa: PLC0415

    host = config.hosts[host_name]
    # Get container name, compose project, and compose service in one call
    cmd = (
        "docker ps -a --format "
        '\'{{.Names}}\t{{.Label "com.docker.compose.project"}}\t'
        '{{.Label "com.docker.compose.service"}}\''
    )
    result = await run_command(host, cmd, stack=host_name, stream=False, prefix="")

    labels: dict[str, tuple[str, str]] = {}
    if result.success:
        for line in result.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 3:  # noqa: PLR2004
                name, stack, service = parts[0], parts[1], parts[2]
                labels[name] = (stack or "", service or "")
    return labels


async def fetch_all_container_stats(
    config: Config,
    port: int = DEFAULT_GLANCES_PORT,
) -> list[ContainerStats]:
    """Fetch container stats from all hosts in parallel, enriched with compose labels."""

    # Fetch Glances stats and compose labels in parallel for each host
    async def fetch_host_data(
        host_name: str,
        host_address: str,
    ) -> list[ContainerStats]:
        # Run both fetches in parallel
        stats_task = fetch_container_stats(host_name, host_address, port)
        labels_task = _fetch_compose_labels(config, host_name)
        containers, labels = await asyncio.gather(stats_task, labels_task)

        # Enrich containers with compose labels
        enriched = []
        for c in containers:
            stack, service = labels.get(c.name, ("", ""))
            enriched.append(
                ContainerStats(
                    name=c.name,
                    host=c.host,
                    status=c.status,
                    image=c.image,
                    cpu_percent=c.cpu_percent,
                    memory_usage=c.memory_usage,
                    memory_limit=c.memory_limit,
                    memory_percent=c.memory_percent,
                    network_rx=c.network_rx,
                    network_tx=c.network_tx,
                    uptime=c.uptime,
                    ports=c.ports,
                    engine=c.engine,
                    stack=stack,
                    service=service,
                )
            )
        return enriched

    tasks = [fetch_host_data(name, host.address) for name, host in config.hosts.items()]
    results = await asyncio.gather(*tasks)
    # Flatten list of lists
    return [container for host_containers in results for container in host_containers]
