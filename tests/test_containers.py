"""Tests for Containers page routes."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from compose_farm.config import Config, Host
from compose_farm.glances import ContainerStats
from compose_farm.web.app import create_app
from compose_farm.web.routes.containers import (
    _format_bytes,
    _infer_stack_service,
    _parse_image,
    _parse_uptime_seconds,
)

# Byte size constants for tests
KB = 1024
MB = KB * 1024
GB = MB * 1024


class TestFormatBytes:
    """Tests for _format_bytes function (uses humanize library)."""

    def test_bytes(self) -> None:
        assert _format_bytes(500) == "500 Bytes"
        assert _format_bytes(0) == "0 Bytes"

    def test_kilobytes(self) -> None:
        assert _format_bytes(KB) == "1.0 KiB"
        assert _format_bytes(KB * 5) == "5.0 KiB"
        assert _format_bytes(KB + 512) == "1.5 KiB"

    def test_megabytes(self) -> None:
        assert _format_bytes(MB) == "1.0 MiB"
        assert _format_bytes(MB * 100) == "100.0 MiB"
        assert _format_bytes(MB * 512) == "512.0 MiB"

    def test_gigabytes(self) -> None:
        assert _format_bytes(GB) == "1.0 GiB"
        assert _format_bytes(GB * 2) == "2.0 GiB"


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


class TestParseUptimeSeconds:
    """Tests for _parse_uptime_seconds function."""

    def test_seconds(self) -> None:
        assert _parse_uptime_seconds("17 seconds") == 17
        assert _parse_uptime_seconds("1 second") == 1

    def test_minutes(self) -> None:
        assert _parse_uptime_seconds("5 minutes") == 300
        assert _parse_uptime_seconds("1 minute") == 60

    def test_hours(self) -> None:
        assert _parse_uptime_seconds("2 hours") == 7200
        assert _parse_uptime_seconds("an hour") == 3600
        assert _parse_uptime_seconds("1 hour") == 3600

    def test_days(self) -> None:
        assert _parse_uptime_seconds("3 days") == 259200
        assert _parse_uptime_seconds("a day") == 86400

    def test_empty(self) -> None:
        assert _parse_uptime_seconds("") == 0
        assert _parse_uptime_seconds("-") == 0


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
            response = client.get("/live-stats")

        assert response.status_code == 200
        assert "Glances not configured" in response.text

    def test_containers_page_with_glances(self, client: TestClient, mock_config: Config) -> None:
        """Test containers page loads when Glances is configured."""
        with patch("compose_farm.web.routes.containers.get_config") as mock:
            mock.return_value = mock_config
            response = client.get("/live-stats")

        assert response.status_code == 200
        assert "Live Stats" in response.text
        assert "container-rows" in response.text


class TestContainersRowsAPI:
    """Tests for containers rows HTML endpoint."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app()
        return TestClient(app)

    def test_rows_without_glances(self, client: TestClient) -> None:
        """Test rows endpoint returns error when Glances not configured."""
        with patch("compose_farm.web.routes.containers.get_config") as mock:
            mock.return_value = Config(
                compose_dir=Path("/opt/compose"),
                hosts={"nas": Host(address="192.168.1.6")},
                stacks={"test": "nas"},
                glances_stack=None,
            )
            response = client.get("/api/containers/rows")

        assert response.status_code == 200
        assert "Glances not configured" in response.text

    def test_rows_returns_html(self, client: TestClient) -> None:
        """Test rows endpoint returns HTML table rows."""
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
                stack="web",
                service="nginx",
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

            response = client.get("/api/containers/rows")

        assert response.status_code == 200
        assert "<tr " in response.text  # <tr id="..."> has attributes
        assert "nginx" in response.text
        assert "running" in response.text

    def test_rows_have_data_sort_attributes(self, client: TestClient) -> None:
        """Test rows have data-sort attributes for client-side sorting."""
        mock_containers = [
            ContainerStats(
                name="alpha",
                host="nas",
                status="running",
                image="nginx:latest",
                cpu_percent=10.0,
                memory_usage=100,
                memory_limit=1000,
                memory_percent=10.0,
                network_rx=100,
                network_tx=100,
                uptime="1 hour",
                ports="",
                engine="docker",
                stack="alpha",
                service="web",
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

            response = client.get("/api/containers/rows")
            assert response.status_code == 200
            # Check that cells have data-sort attributes
            assert 'data-sort="alpha"' in response.text  # stack
            assert 'data-sort="web"' in response.text  # service
            assert 'data-sort="3600"' in response.text  # uptime (1 hour = 3600s)
            assert 'data-sort="10' in response.text  # cpu
