"""Tests for compose file parsing utilities."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from compose_farm.compose import extract_website_urls, get_container_name
from compose_farm.config import Config, Host


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


class TestExtractWebsiteUrls:
    """Test extract_website_urls function."""

    def _create_config(self, tmp_path: Path) -> Config:
        """Create a test config."""
        return Config(
            compose_dir=tmp_path,
            hosts={"nas": Host(address="192.168.1.10")},
            stacks={"mystack": "nas"},
        )

    def test_extract_https_url(self, tmp_path: Path) -> None:
        """Extracts HTTPS URL from websecure entrypoint."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.web.rule": "Host(`app.example.com`)",
                        "traefik.http.routers.web.entrypoints": "websecure",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://app.example.com"]

    def test_extract_http_url(self, tmp_path: Path) -> None:
        """Extracts HTTP URL from web entrypoint."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.web.rule": "Host(`app.local`)",
                        "traefik.http.routers.web.entrypoints": "web",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["http://app.local"]

    def test_extract_multiple_urls(self, tmp_path: Path) -> None:
        """Extracts multiple URLs from different routers."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.web.rule": "Host(`app.example.com`)",
                        "traefik.http.routers.web.entrypoints": "websecure",
                        "traefik.http.routers.web-local.rule": "Host(`app.local`)",
                        "traefik.http.routers.web-local.entrypoints": "web",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["http://app.local", "https://app.example.com"]

    def test_https_preferred_over_http(self, tmp_path: Path) -> None:
        """HTTPS is preferred when same host has both."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        # Same host with different entrypoints
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.web-http.rule": "Host(`app.example.com`)",
                        "traefik.http.routers.web-http.entrypoints": "web",
                        "traefik.http.routers.web-https.rule": "Host(`app.example.com`)",
                        "traefik.http.routers.web-https.entrypoints": "websecure",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://app.example.com"]

    def test_traefik_disabled(self, tmp_path: Path) -> None:
        """Returns empty list when traefik.enable is false."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "false",
                        "traefik.http.routers.web.rule": "Host(`app.example.com`)",
                        "traefik.http.routers.web.entrypoints": "websecure",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == []

    def test_no_traefik_labels(self, tmp_path: Path) -> None:
        """Returns empty list when no traefik labels."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == []

    def test_compose_file_not_exists(self, tmp_path: Path) -> None:
        """Returns empty list when compose file doesn't exist."""
        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == []

    def test_env_variable_interpolation(self, tmp_path: Path) -> None:
        """Interpolates environment variables in host rule."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        env_file = stack_dir / ".env"

        env_file.write_text("DOMAIN=example.com\n")
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.web.rule": "Host(`app.${DOMAIN}`)",
                        "traefik.http.routers.web.entrypoints": "websecure",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://app.example.com"]
