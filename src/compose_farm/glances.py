"""Glances API client for host resource monitoring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
