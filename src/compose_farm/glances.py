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
    cpu_name: str
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
            cpu_name="",
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

    url = f"http://{host_address}:{port}/api/4/quicklook"

    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            response = await client.get(url)
            if not response.is_success:
                return HostStats.from_error(host_name, f"HTTP {response.status_code}")
            data = response.json()
            return HostStats(
                host=host_name,
                cpu_percent=data.get("cpu", 0),
                mem_percent=data.get("mem", 0),
                swap_percent=data.get("swap", 0),
                load=data.get("load", 0),
                cpu_name=data.get("cpu_name", ""),
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
