"""Tests for Containers page routes."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from compose_farm.config import Config, Host
from compose_farm.glances import ContainerStats
from compose_farm.web.app import create_app
from compose_farm.web.routes.containers import (
    GB,
    KB,
    MB,
    _format_bytes,
    _infer_stack_service,
    _parse_image,
    container_to_dict,
)


class TestFormatBytes:
    """Tests for _format_bytes function."""

    def test_bytes(self) -> None:
        assert _format_bytes(500) == "500B"
        assert _format_bytes(0) == "0B"

    def test_kilobytes(self) -> None:
        assert _format_bytes(KB) == "1.0KB"
        assert _format_bytes(KB * 5) == "5.0KB"
        assert _format_bytes(KB + 512) == "1.5KB"

    def test_megabytes(self) -> None:
        assert _format_bytes(MB) == "1.0MB"
        assert _format_bytes(MB * 100) == "100.0MB"
        assert _format_bytes(MB * 512) == "512.0MB"

    def test_gigabytes(self) -> None:
        assert _format_bytes(GB) == "1.0GB"
        assert _format_bytes(GB * 2) == "2.0GB"


class TestParseImage:
    """Tests for _parse_image function."""

    def test_simple_image_with_tag(self) -> None:
        assert _parse_image("nginx:latest") == ("nginx", "latest")
        assert _parse_image("redis:7") == ("redis", "7")

    def test_image_without_tag(self) -> None:
        assert _parse_image("nginx") == ("nginx", "latest")

    def test_registry_image(self) -> None:
        assert _parse_image("ghcr.io/user/repo:v1.0") == ("ghcr.io/user/repo", "v1.0")
        assert _parse_image("docker.io/library/nginx:alpine") == (
            "docker.io/library/nginx",
            "alpine",
        )

    def test_image_with_port_in_registry(self) -> None:
        # Registry with port should not be confused with tag
        assert _parse_image("localhost:5000/myimage") == ("localhost:5000/myimage", "latest")


class TestInferStackService:
    """Tests for _infer_stack_service function."""

    def test_underscore_separator(self) -> None:
        assert _infer_stack_service("mystack_web_1") == ("mystack", "web")
        assert _infer_stack_service("app_db_1") == ("app", "db")

    def test_hyphen_separator(self) -> None:
        assert _infer_stack_service("mystack-web-1") == ("mystack", "web")
        assert _infer_stack_service("compose-farm-api-1") == ("compose", "farm-api")

    def test_simple_name(self) -> None:
        # No separator - use name for both
        assert _infer_stack_service("nginx") == ("nginx", "nginx")
        assert _infer_stack_service("traefik") == ("traefik", "traefik")

    def test_single_part_with_separator(self) -> None:
        # Edge case: separator with empty second part
        assert _infer_stack_service("single_") == ("single", "")


class TestContainerToDict:
    """Tests for container_to_dict function."""

    def test_basic_conversion(self) -> None:
        stats = ContainerStats(
            name="mystack-web-1",
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

        result = container_to_dict(stats)

        assert result["name"] == "mystack-web-1"
        assert result["stack"] == "mystack"
        assert result["service"] == "web"
        assert result["host"] == "nas"
        assert result["image"] == "nginx"
        assert result["tag"] == "latest"
        assert result["status"] == "running"
        assert result["uptime"] == "2 hours"
        assert result["cpu_percent"] == 5.5
        assert result["memory_usage"] == "100.0MB"
        assert result["memory_percent"] == 9.8  # Rounded
        assert result["net_io"] == "↓976.6KB ↑488.3KB"
        assert result["ports"] == "80->80/tcp"


class TestContainersPage:
    """Tests for containers page endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app()
        return TestClient(app)

    @pytest.fixture
    def mock_config(self) -> Config:
        return Config(
            compose_dir=Path("/opt/compose"),
            hosts={
                "nas": Host(address="192.168.1.6"),
                "nuc": Host(address="192.168.1.2"),
            },
            stacks={"test": "nas"},
            glances_stack="glances",
        )

    def test_containers_page_without_glances(self, client: TestClient) -> None:
        """Test containers page shows warning when Glances not configured."""
        with patch("compose_farm.web.routes.containers.get_config") as mock:
            mock.return_value = Config(
                compose_dir=Path("/opt/compose"),
                hosts={"nas": Host(address="192.168.1.6")},
                stacks={"test": "nas"},
                glances_stack=None,
            )
            response = client.get("/containers")

        assert response.status_code == 200
        assert "Glances not configured" in response.text

    def test_containers_page_with_glances(self, client: TestClient, mock_config: Config) -> None:
        """Test containers page loads when Glances is configured."""
        with patch("compose_farm.web.routes.containers.get_config") as mock:
            mock.return_value = mock_config
            response = client.get("/containers")

        assert response.status_code == 200
        assert "Containers" in response.text
        assert "containers-table" in response.text


class TestContainersAPI:
    """Tests for containers API endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app()
        return TestClient(app)

    def test_api_without_glances(self, client: TestClient) -> None:
        """Test API returns error when Glances not configured."""
        with patch("compose_farm.web.routes.containers.get_config") as mock:
            mock.return_value = Config(
                compose_dir=Path("/opt/compose"),
                hosts={"nas": Host(address="192.168.1.6")},
                stacks={"test": "nas"},
                glances_stack=None,
            )
            response = client.get("/api/containers/list")

        assert response.status_code == 200
        data = response.json()
        assert data["error"] == "Glances not configured"
        assert data["data"] == []

    def test_api_with_containers(self, client: TestClient) -> None:
        """Test API returns container data."""
        mock_containers = [
            ContainerStats(
                name="nginx",
                host="nas",
                status="running",
                image="nginx:latest",
                cpu_percent=5.5,
                memory_usage=104857600,
                memory_limit=1073741824,
                memory_percent=9.77,
                network_rx=1000,
                network_tx=500,
                uptime="2 hours",
                ports="80->80/tcp",
                engine="docker",
            ),
            ContainerStats(
                name="redis",
                host="nas",
                status="running",
                image="redis:7",
                cpu_percent=1.2,
                memory_usage=52428800,
                memory_limit=1073741824,
                memory_percent=4.88,
                network_rx=500,
                network_tx=200,
                uptime="3 hours",
                ports="",
                engine="docker",
            ),
        ]

        with (
            patch("compose_farm.web.routes.containers.get_config") as mock_config,
            patch(
                "compose_farm.web.routes.containers.fetch_all_container_stats",
                new_callable=AsyncMock,
            ) as mock_fetch,
        ):
            mock_config.return_value = Config(
                compose_dir=Path("/opt/compose"),
                hosts={"nas": Host(address="192.168.1.6")},
                stacks={"test": "nas"},
                glances_stack="glances",
            )
            mock_fetch.return_value = mock_containers

            response = client.get("/api/containers/list")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["data"]) == 2
        # Should be sorted by CPU (highest first)
        assert data["data"][0]["name"] == "nginx"
        assert data["data"][0]["cpu_percent"] == 5.5
        assert data["data"][1]["name"] == "redis"
