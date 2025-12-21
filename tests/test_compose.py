"""Tests for compose file parsing utilities."""

from __future__ import annotations

import pytest

from compose_farm.compose import get_container_name


class TestGetContainerName:
    """Test get_container_name helper function."""

    def test_explicit_container_name(self) -> None:
        """Uses container_name from service definition when set."""
        service_def = {"image": "nginx", "container_name": "my-custom-name"}
        result = get_container_name("web", service_def, "myproject")
        assert result == "my-custom-name"

    def test_default_naming_pattern(self) -> None:
        """Falls back to {project}-{service}-1 pattern."""
        service_def = {"image": "nginx"}
        result = get_container_name("web", service_def, "myproject")
        assert result == "myproject-web-1"

    def test_none_service_def(self) -> None:
        """Handles None service definition gracefully."""
        result = get_container_name("web", None, "myproject")
        assert result == "myproject-web-1"

    def test_empty_service_def(self) -> None:
        """Handles empty service definition."""
        result = get_container_name("web", {}, "myproject")
        assert result == "myproject-web-1"

    def test_container_name_none_value(self) -> None:
        """Handles container_name set to None."""
        service_def = {"image": "nginx", "container_name": None}
        result = get_container_name("web", service_def, "myproject")
        assert result == "myproject-web-1"

    def test_container_name_empty_string(self) -> None:
        """Handles container_name set to empty string."""
        service_def = {"image": "nginx", "container_name": ""}
        result = get_container_name("web", service_def, "myproject")
        assert result == "myproject-web-1"

    @pytest.mark.parametrize(
        ("service_name", "project_name", "expected"),
        [
            ("redis", "plex", "plex-redis-1"),
            ("plex-server", "media", "media-plex-server-1"),
            ("db", "my-app", "my-app-db-1"),
        ],
    )
    def test_various_naming_combinations(
        self, service_name: str, project_name: str, expected: str
    ) -> None:
        """Test various service/project name combinations."""
        result = get_container_name(service_name, {"image": "test"}, project_name)
        assert result == expected
