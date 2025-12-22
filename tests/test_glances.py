"""Tests for Glances integration."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from compose_farm.config import Config, Host
from compose_farm.glances import (
    DEFAULT_GLANCES_PORT,
    HostStats,
    fetch_all_host_stats,
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
            cpu_name="Intel Core i5",
        )
        assert stats.host == "nas"
        assert stats.cpu_percent == 25.5
        assert stats.mem_percent == 50.0
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
        mock_response = httpx.Response(
            200,
            json={
                "cpu": 25.5,
                "mem": 50.0,
                "swap": 5.0,
                "load": 2.5,
                "cpu_name": "Intel Core i5",
            },
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            stats = await fetch_host_stats("nas", "192.168.1.6")

        assert stats.host == "nas"
        assert stats.cpu_percent == 25.5
        assert stats.mem_percent == 50.0
        assert stats.swap_percent == 5.0
        assert stats.load == 2.5
        assert stats.cpu_name == "Intel Core i5"
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

        mock_response = httpx.Response(
            200,
            json={
                "cpu": 25.5,
                "mem": 50.0,
                "swap": 5.0,
                "load": 2.5,
                "cpu_name": "Intel Core i5",
            },
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            stats = await fetch_all_host_stats(config)

        assert "nas" in stats
        assert "nuc" in stats
        assert stats["nas"].cpu_percent == 25.5
        assert stats["nuc"].cpu_percent == 25.5


class TestDefaultPort:
    """Tests for default Glances port constant."""

    def test_default_port(self) -> None:
        assert DEFAULT_GLANCES_PORT == 61208
