"""Tests for Glances integration."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from compose_farm.config import Config, Host
from compose_farm.glances import (
    DEFAULT_GLANCES_PORT,
    ContainerStats,
    HostStats,
    _get_glances_address,
    fetch_all_container_stats,
    fetch_all_host_stats,
    fetch_container_stats,
    fetch_host_stats,
)


class TestHostStats:
    """Tests for HostStats dataclass."""

    def test_host_stats_creation(self) -> None:
        stats = HostStats(
            host="nas",
            cpu_percent=25.5,
            mem_percent=50.0,
            swap_percent=10.0,
            load=2.5,
            disk_percent=75.0,
        )
        assert stats.host == "nas"
        assert stats.cpu_percent == 25.5
        assert stats.mem_percent == 50.0
        assert stats.disk_percent == 75.0
        assert stats.error is None

    def test_host_stats_from_error(self) -> None:
        stats = HostStats.from_error("nas", "Connection refused")
        assert stats.host == "nas"
        assert stats.cpu_percent == 0
        assert stats.mem_percent == 0
        assert stats.error == "Connection refused"


class TestFetchHostStats:
    """Tests for fetch_host_stats function."""

    @pytest.mark.asyncio
    async def test_fetch_host_stats_success(self) -> None:
        quicklook_response = httpx.Response(
            200,
            json={
                "cpu": 25.5,
                "mem": 50.0,
                "swap": 5.0,
                "load": 2.5,
            },
        )
        fs_response = httpx.Response(
            200,
            json=[
                {"mnt_point": "/", "percent": 65.0},
                {"mnt_point": "/mnt/data", "percent": 80.0},
            ],
        )

        async def mock_get(url: str) -> httpx.Response:
            if "quicklook" in url:
                return quicklook_response
            return fs_response

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(side_effect=mock_get)

            stats = await fetch_host_stats("nas", "192.168.1.6")

        assert stats.host == "nas"
        assert stats.cpu_percent == 25.5
        assert stats.mem_percent == 50.0
        assert stats.swap_percent == 5.0
        assert stats.load == 2.5
        assert stats.disk_percent == 65.0  # Root filesystem
        assert stats.error is None

    @pytest.mark.asyncio
    async def test_fetch_host_stats_http_error(self) -> None:
        mock_response = httpx.Response(500)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            stats = await fetch_host_stats("nas", "192.168.1.6")

        assert stats.host == "nas"
        assert stats.error == "HTTP 500"
        assert stats.cpu_percent == 0

    @pytest.mark.asyncio
    async def test_fetch_host_stats_timeout(self) -> None:
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

            stats = await fetch_host_stats("nas", "192.168.1.6")

        assert stats.host == "nas"
        assert stats.error == "timeout"

    @pytest.mark.asyncio
    async def test_fetch_host_stats_connection_error(self) -> None:
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )

            stats = await fetch_host_stats("nas", "192.168.1.6")

        assert stats.host == "nas"
        assert stats.error is not None
        assert "Connection refused" in stats.error


class TestFetchAllHostStats:
    """Tests for fetch_all_host_stats function."""

    @pytest.mark.asyncio
    async def test_fetch_all_host_stats(self) -> None:
        config = Config(
            compose_dir=Path("/opt/compose"),
            hosts={
                "nas": Host(address="192.168.1.6"),
                "nuc": Host(address="192.168.1.2"),
            },
            stacks={"test": "nas"},
        )

        quicklook_response = httpx.Response(
            200,
            json={
                "cpu": 25.5,
                "mem": 50.0,
                "swap": 5.0,
                "load": 2.5,
            },
        )
        fs_response = httpx.Response(
            200,
            json=[{"mnt_point": "/", "percent": 70.0}],
        )

        async def mock_get(url: str) -> httpx.Response:
            if "quicklook" in url:
                return quicklook_response
            return fs_response

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(side_effect=mock_get)

            stats = await fetch_all_host_stats(config)

        assert "nas" in stats
        assert "nuc" in stats
        assert stats["nas"].cpu_percent == 25.5
        assert stats["nuc"].cpu_percent == 25.5
        assert stats["nas"].disk_percent == 70.0


class TestDefaultPort:
    """Tests for default Glances port constant."""

    def test_default_port(self) -> None:
        assert DEFAULT_GLANCES_PORT == 61208


class TestContainerStats:
    """Tests for ContainerStats dataclass."""

    def test_container_stats_creation(self) -> None:
        stats = ContainerStats(
            name="nginx",
            host="nas",
            status="running",
            image="nginx:latest",
            cpu_percent=5.5,
            memory_usage=104857600,  # 100MB
            memory_limit=1073741824,  # 1GB
            memory_percent=9.77,
            network_rx=1000000,
            network_tx=500000,
            uptime="2 hours",
            ports="80->80/tcp",
            engine="docker",
        )
        assert stats.name == "nginx"
        assert stats.host == "nas"
        assert stats.cpu_percent == 5.5


class TestFetchContainerStats:
    """Tests for fetch_container_stats function."""

    @pytest.mark.asyncio
    async def test_fetch_container_stats_success(self) -> None:
        mock_response = httpx.Response(
            200,
            json=[
                {
                    "name": "nginx",
                    "status": "running",
                    "image": ["nginx:latest"],
                    "cpu_percent": 5.5,
                    "memory_usage": 104857600,
                    "memory_limit": 1073741824,
                    "network": {"cumulative_rx": 1000, "cumulative_tx": 500},
                    "uptime": "2 hours",
                    "ports": "80->80/tcp",
                    "engine": "docker",
                },
                {
                    "name": "redis",
                    "status": "running",
                    "image": ["redis:7"],
                    "cpu_percent": 1.2,
                    "memory_usage": 52428800,
                    "memory_limit": 1073741824,
                    "network": {},
                    "uptime": "3 hours",
                    "ports": "",
                    "engine": "docker",
                },
            ],
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            containers, error = await fetch_container_stats("nas", "192.168.1.6")

        assert error is None
        assert containers is not None
        assert len(containers) == 2
        assert containers[0].name == "nginx"
        assert containers[0].host == "nas"
        assert containers[0].cpu_percent == 5.5
        assert containers[1].name == "redis"

    @pytest.mark.asyncio
    async def test_fetch_container_stats_empty_on_error(self) -> None:
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

            containers, error = await fetch_container_stats("nas", "192.168.1.6")

        assert containers is None
        assert error == "Connection timed out"

    @pytest.mark.asyncio
    async def test_fetch_container_stats_handles_string_image(self) -> None:
        """Test that image field works as string (not just list)."""
        mock_response = httpx.Response(
            200,
            json=[
                {
                    "name": "test",
                    "status": "running",
                    "image": "myimage:v1",  # String instead of list
                    "cpu_percent": 0,
                    "memory_usage": 0,
                    "memory_limit": 1,
                    "network": {},
                    "uptime": "",
                    "ports": "",
                    "engine": "docker",
                },
            ],
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            containers, error = await fetch_container_stats("nas", "192.168.1.6")

        assert error is None
        assert containers is not None
        assert len(containers) == 1
        assert containers[0].image == "myimage:v1"


class TestFetchAllContainerStats:
    """Tests for fetch_all_container_stats function."""

    @pytest.mark.asyncio
    async def test_fetch_all_container_stats(self) -> None:
        config = Config(
            compose_dir=Path("/opt/compose"),
            hosts={
                "nas": Host(address="192.168.1.6"),
                "nuc": Host(address="192.168.1.2"),
            },
            stacks={"test": "nas"},
        )

        mock_response = httpx.Response(
            200,
            json=[
                {
                    "name": "nginx",
                    "status": "running",
                    "image": ["nginx:latest"],
                    "cpu_percent": 5.5,
                    "memory_usage": 104857600,
                    "memory_limit": 1073741824,
                    "network": {},
                    "uptime": "2 hours",
                    "ports": "",
                    "engine": "docker",
                },
            ],
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            containers = await fetch_all_container_stats(config)

        # 2 hosts x 1 container each = 2 containers
        assert len(containers) == 2
        hosts = {c.host for c in containers}
        assert "nas" in hosts
        assert "nuc" in hosts


class TestGetGlancesAddress:
    """Tests for _get_glances_address function."""

    def test_returns_host_address_outside_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without CF_WEB_STACK, always return host address."""
        monkeypatch.delenv("CF_WEB_STACK", raising=False)
        monkeypatch.delenv("CF_LOCAL_HOST", raising=False)
        host = Host(address="192.168.1.6")
        result = _get_glances_address("nas", host, "glances")
        assert result == "192.168.1.6"

    def test_returns_host_address_without_glances_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In container without glances_stack config, return host address."""
        monkeypatch.setenv("CF_WEB_STACK", "compose-farm")
        monkeypatch.delenv("CF_LOCAL_HOST", raising=False)
        host = Host(address="192.168.1.6")
        result = _get_glances_address("nas", host, None)
        assert result == "192.168.1.6"

    def test_returns_container_name_for_explicit_local_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CF_LOCAL_HOST explicitly marks which host uses container name."""
        monkeypatch.setenv("CF_WEB_STACK", "compose-farm")
        monkeypatch.setenv("CF_LOCAL_HOST", "nas")
        host = Host(address="192.168.1.6")
        result = _get_glances_address("nas", host, "glances")
        assert result == "glances"

    def test_returns_host_address_for_non_local_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-local hosts use their IP address even in container mode."""
        monkeypatch.setenv("CF_WEB_STACK", "compose-farm")
        monkeypatch.setenv("CF_LOCAL_HOST", "nas")
        host = Host(address="192.168.1.2")
        result = _get_glances_address("nuc", host, "glances")
        assert result == "192.168.1.2"

    def test_fallback_to_is_local_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without CF_LOCAL_HOST, falls back to is_local detection."""
        monkeypatch.setenv("CF_WEB_STACK", "compose-farm")
        monkeypatch.delenv("CF_LOCAL_HOST", raising=False)
        # Use localhost which should be detected as local
        host = Host(address="localhost")
        result = _get_glances_address("local", host, "glances")
        assert result == "glances"

    def test_remote_host_not_affected_by_container_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Remote hosts always use their IP, even in container mode."""
        monkeypatch.setenv("CF_WEB_STACK", "compose-farm")
        monkeypatch.delenv("CF_LOCAL_HOST", raising=False)
        host = Host(address="192.168.1.100")
        result = _get_glances_address("remote", host, "glances")
        assert result == "192.168.1.100"
