"""Glances API client for host resource monitoring."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .executor import is_local

if TYPE_CHECKING:
    from .config import Config, Host

# Default Glances REST API port
DEFAULT_GLANCES_PORT = 61208


def _get_glances_address(
    host_name: str,
    host: Host,
    glances_container: str | None,
) -> str:
    """Get the address to use for Glances API requests.

    When running in a Docker container (CF_WEB_STACK set), the local host's Glances
    may not be reachable via its LAN IP due to Docker network isolation. In this case,
    we use the Glances container name for the local host.
    Set CF_LOCAL_HOST=<hostname> to explicitly specify which host is local.
    """
    # Only use container name when running inside a Docker container
    in_container = os.environ.get("CF_WEB_STACK") is not None
    if not in_container or not glances_container:
        return host.address

    # CF_LOCAL_HOST explicitly tells us which host to reach via container name
    explicit_local = os.environ.get("CF_LOCAL_HOST")
    if explicit_local and host_name == explicit_local:
        return glances_container

    # Fall back to is_local detection (may not work in container)
    if is_local(host):
        return glances_container

    return host.address


@dataclass
class HostStats:
    """Resource statistics for a host."""

    host: str
    cpu_percent: float
    mem_percent: float
    swap_percent: float
    load: float
    disk_percent: float
    net_rx_rate: float = 0.0  # bytes/sec
    net_tx_rate: float = 0.0  # bytes/sec
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
            net_rx_rate=0,
            net_tx_rate=0,
            error=error,
        )


async def fetch_host_stats(
    host_name: str,
    host_address: str,
    port: int = DEFAULT_GLANCES_PORT,
    request_timeout: float = 10.0,
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

            # Fetch filesystem stats for disk usage (root fs or max across all)
            disk_percent = 0.0
            try:
                fs_response = await client.get(f"{base_url}/fs")
                if fs_response.is_success:
                    fs_data = fs_response.json()
                    root = next((fs for fs in fs_data if fs.get("mnt_point") == "/"), None)
                    disk_percent = (
                        root.get("percent", 0)
                        if root
                        else max((fs.get("percent", 0) for fs in fs_data), default=0)
                    )
            except httpx.HTTPError:
                pass  # Disk stats are optional

            # Fetch network stats for rate (sum across non-loopback interfaces)
            net_rx_rate, net_tx_rate = 0.0, 0.0
            try:
                net_response = await client.get(f"{base_url}/network")
                if net_response.is_success:
                    for iface in net_response.json():
                        if not iface.get("interface_name", "").startswith("lo"):
                            net_rx_rate += iface.get("bytes_recv_rate_per_sec") or 0
                            net_tx_rate += iface.get("bytes_sent_rate_per_sec") or 0
            except httpx.HTTPError:
                pass  # Network stats are optional

            return HostStats(
                host=host_name,
                cpu_percent=data.get("cpu", 0),
                mem_percent=data.get("mem", 0),
                swap_percent=data.get("swap", 0),
                load=data.get("load", 0),
                disk_percent=disk_percent,
                net_rx_rate=net_rx_rate,
                net_tx_rate=net_tx_rate,
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
    glances_container = config.glances_stack
    tasks = [
        fetch_host_stats(name, _get_glances_address(name, host, glances_container), port)
        for name, host in config.hosts.items()
    ]
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
    request_timeout: float = 10.0,
) -> tuple[list[ContainerStats] | None, str | None]:
    """Fetch container stats from a single host's Glances API.

    Returns:
        (containers, error_message)
        - Success: ([...], None)
        - Failure: (None, "error message")

    """
    import httpx  # noqa: PLC0415

    url = f"http://{host_address}:{port}/api/4/containers"

    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            response = await client.get(url)
            if not response.is_success:
                return None, f"HTTP {response.status_code}: {response.reason_phrase}"
            data = response.json()
            return [_parse_container(c, host_name) for c in data], None
    except httpx.ConnectError:
        return None, "Connection refused (Glances offline?)"
    except httpx.TimeoutException:
        return None, "Connection timed out"
    except Exception as e:
        return None, str(e)


async def fetch_all_container_stats(
    config: Config,
    port: int = DEFAULT_GLANCES_PORT,
) -> list[ContainerStats]:
    """Fetch container stats from all hosts in parallel, enriched with compose labels."""
    from .executor import get_container_compose_labels  # noqa: PLC0415

    glances_container = config.glances_stack

    async def fetch_host_data(
        host_name: str,
        host_address: str,
    ) -> list[ContainerStats]:
        # Fetch Glances stats and compose labels in parallel
        stats_task = fetch_container_stats(host_name, host_address, port)
        labels_task = get_container_compose_labels(config, host_name)
        (containers, _), labels = await asyncio.gather(stats_task, labels_task)

        if containers is None:
            # Skip failed hosts in aggregate view
            return []

        # Enrich containers with compose labels (mutate in place)
        for c in containers:
            c.stack, c.service = labels.get(c.name, ("", ""))
        return containers

    tasks = [
        fetch_host_data(name, _get_glances_address(name, host, glances_container))
        for name, host in config.hosts.items()
    ]
    results = await asyncio.gather(*tasks)
    # Flatten list of lists
    return [container for host_containers in results for container in host_containers]
