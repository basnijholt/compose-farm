"""Tests for Traefik config generator."""

from pathlib import Path

import yaml

from compose_farm.compose import parse_external_networks
from compose_farm.config import Config, Host
from compose_farm.traefik import extract_website_urls, generate_traefik_config


def _write_compose(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_generate_traefik_config_with_published_port(tmp_path: Path) -> None:
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"nas01": Host(address="192.168.1.10")},
        stacks={"plex": "nas01"},
    )
    compose_path = tmp_path / "plex" / "docker-compose.yml"
    _write_compose(
        compose_path,
        {
            "services": {
                "plex": {
                    "ports": ["32400:32400"],
                    "labels": [
                        "traefik.enable=true",
                        "traefik.http.routers.plex.rule=Host(`plex.lab.mydomain.org`)",
                        "traefik.http.routers.plex.entrypoints=web,websecure",
                        "traefik.http.routers.plex.tls.domains[0].main=plex.lab.mydomain.org",
                        "traefik.http.services.plex.loadbalancer.server.port=32400",
                    ],
                }
            }
        },
    )

    dynamic, warnings = generate_traefik_config(cfg, ["plex"])

    assert warnings == []
    assert dynamic["http"]["routers"]["plex"]["rule"] == "Host(`plex.lab.mydomain.org`)"
    assert dynamic["http"]["routers"]["plex"]["entrypoints"] == ["web", "websecure"]
    assert (
        dynamic["http"]["routers"]["plex"]["tls"]["domains"][0]["main"] == "plex.lab.mydomain.org"
    )

    servers = dynamic["http"]["services"]["plex"]["loadbalancer"]["servers"]
    assert servers == [{"url": "http://192.168.1.10:32400"}]


def test_generate_traefik_config_without_published_port_warns(tmp_path: Path) -> None:
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"nas01": Host(address="192.168.1.10")},
        stacks={"app": "nas01"},
    )
    compose_path = tmp_path / "app" / "docker-compose.yml"
    _write_compose(
        compose_path,
        {
            "services": {
                "app": {
                    "ports": ["8080"],
                    "labels": [
                        "traefik.http.routers.app.rule=Host(`app.lab.mydomain.org`)",
                        "traefik.http.services.app.loadbalancer.server.port=8080",
                    ],
                }
            }
        },
    )

    dynamic, warnings = generate_traefik_config(cfg, ["app"])

    assert dynamic["http"]["routers"]["app"]["rule"] == "Host(`app.lab.mydomain.org`)"
    assert any("No published port found" in warning for warning in warnings)


def test_generate_interpolates_env_and_infers_router_service(tmp_path: Path) -> None:
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"nas01": Host(address="192.168.1.10")},
        stacks={"wakapi": "nas01"},
    )
    compose_dir = tmp_path / "wakapi"
    compose_dir.mkdir(parents=True, exist_ok=True)
    (compose_dir / ".env").write_text("DOMAIN=lab.mydomain.org\n")
    compose_path = compose_dir / "docker-compose.yml"
    _write_compose(
        compose_path,
        {
            "services": {
                "wakapi": {
                    "ports": ["3009:3000"],
                    "labels": [
                        "traefik.enable=true",
                        "traefik.http.routers.wakapi.rule=Host(`wakapi.${DOMAIN}`)",
                        "traefik.http.routers.wakapi.entrypoints=websecure",
                        "traefik.http.routers.wakapi-local.rule=Host(`wakapi.local`)",
                        "traefik.http.routers.wakapi-local.entrypoints=web",
                        "traefik.http.services.wakapi.loadbalancer.server.port=3000",
                    ],
                }
            }
        },
    )

    dynamic, warnings = generate_traefik_config(cfg, ["wakapi"])

    assert warnings == []
    routers = dynamic["http"]["routers"]
    assert routers["wakapi"]["rule"] == "Host(`wakapi.lab.mydomain.org`)"
    assert routers["wakapi"]["entrypoints"] == ["websecure"]
    assert routers["wakapi-local"]["entrypoints"] == ["web"]
    assert routers["wakapi-local"]["service"] == "wakapi"

    servers = dynamic["http"]["services"]["wakapi"]["loadbalancer"]["servers"]
    assert servers == [{"url": "http://192.168.1.10:3009"}]


def test_generate_interpolates_label_keys_and_ports(tmp_path: Path) -> None:
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"nas01": Host(address="192.168.1.10")},
        stacks={"supabase": "nas01"},
    )
    compose_dir = tmp_path / "supabase"
    compose_dir.mkdir(parents=True, exist_ok=True)
    (compose_dir / ".env").write_text(
        "CONTAINER_PREFIX=supa\n"
        "SUBDOMAIN=api\n"
        "DOMAIN=lab.mydomain.org\n"
        "PUBLIC_DOMAIN=public.example.org\n"
        "KONG_HTTP_PORT=8000\n"
    )
    compose_path = compose_dir / "docker-compose.yml"
    _write_compose(
        compose_path,
        {
            "services": {
                "kong": {
                    "ports": ["${KONG_HTTP_PORT}:8000/tcp"],
                    "labels": [
                        "traefik.enable=true",
                        "traefik.http.routers.${CONTAINER_PREFIX}.rule=Host(`${SUBDOMAIN}.${DOMAIN}`) || Host(`${SUBDOMAIN}.${PUBLIC_DOMAIN}`)",
                        "traefik.http.routers.${CONTAINER_PREFIX}-studio.rule=Host(`studio.${DOMAIN}`)",
                        "traefik.http.services.${CONTAINER_PREFIX}.loadbalancer.server.port=8000",
                    ],
                }
            }
        },
    )

    dynamic, warnings = generate_traefik_config(cfg, ["supabase"])

    assert warnings == []
    routers = dynamic["http"]["routers"]
    assert "supa" in routers
    assert "supa-studio" in routers
    assert routers["supa"]["service"] == "supa"
    assert routers["supa-studio"]["service"] == "supa"
    servers = dynamic["http"]["services"]["supa"]["loadbalancer"]["servers"]
    assert servers == [{"url": "http://192.168.1.10:8000"}]


def test_generate_skips_services_with_enable_false(tmp_path: Path) -> None:
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"nas01": Host(address="192.168.1.10")},
        stacks={"stack": "nas01"},
    )
    compose_path = tmp_path / "stack" / "docker-compose.yml"
    _write_compose(
        compose_path,
        {
            "services": {
                "studio": {
                    "ports": ["3000:3000"],
                    "labels": [
                        "traefik.enable=false",
                        "traefik.http.routers.studio.rule=Host(`studio.lab.mydomain.org`)",
                        "traefik.http.services.studio.loadbalancer.server.port=3000",
                    ],
                }
            }
        },
    )

    dynamic, warnings = generate_traefik_config(cfg, ["stack"])

    assert dynamic == {}
    assert warnings == []


def test_generate_follows_network_mode_service_for_ports(tmp_path: Path) -> None:
    """Services using network_mode: service:X should use ports from service X."""
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"nas01": Host(address="192.168.1.10")},
        stacks={"vpn-stack": "nas01"},
    )
    compose_path = tmp_path / "vpn-stack" / "docker-compose.yml"
    _write_compose(
        compose_path,
        {
            "services": {
                "vpn": {
                    "image": "gluetun",
                    "ports": ["5080:5080", "9696:9696"],
                },
                "syncthing": {
                    "image": "syncthing",
                    "network_mode": "service:vpn",
                    "labels": [
                        "traefik.enable=true",
                        "traefik.http.routers.sync.rule=Host(`sync.example.com`)",
                        "traefik.http.services.sync.loadbalancer.server.port=5080",
                    ],
                },
                "searxng": {
                    "image": "searxng",
                    "network_mode": "service:vpn",
                    "labels": [
                        "traefik.enable=true",
                        "traefik.http.routers.searxng.rule=Host(`searxng.example.com`)",
                        "traefik.http.services.searxng.loadbalancer.server.port=9696",
                    ],
                },
            }
        },
    )

    dynamic, warnings = generate_traefik_config(cfg, ["vpn-stack"])

    assert warnings == []
    # Both services should get their ports from the vpn service
    sync_servers = dynamic["http"]["services"]["sync"]["loadbalancer"]["servers"]
    assert sync_servers == [{"url": "http://192.168.1.10:5080"}]
    searxng_servers = dynamic["http"]["services"]["searxng"]["loadbalancer"]["servers"]
    assert searxng_servers == [{"url": "http://192.168.1.10:9696"}]


def test_parse_external_networks_single(tmp_path: Path) -> None:
    """Extract a single external network from compose file."""
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="192.168.1.10")},
        stacks={"app": "host1"},
    )
    compose_path = tmp_path / "app" / "compose.yaml"
    _write_compose(
        compose_path,
        {
            "services": {"app": {"image": "nginx"}},
            "networks": {"mynetwork": {"external": True}},
        },
    )

    networks = parse_external_networks(cfg, "app")
    assert networks == ["mynetwork"]


def test_parse_external_networks_multiple(tmp_path: Path) -> None:
    """Extract multiple external networks from compose file."""
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="192.168.1.10")},
        stacks={"app": "host1"},
    )
    compose_path = tmp_path / "app" / "compose.yaml"
    _write_compose(
        compose_path,
        {
            "services": {"app": {"image": "nginx"}},
            "networks": {
                "frontend": {"external": True},
                "backend": {"external": True},
                "internal": {"driver": "bridge"},  # not external
            },
        },
    )

    networks = parse_external_networks(cfg, "app")
    assert set(networks) == {"frontend", "backend"}


def test_parse_external_networks_none(tmp_path: Path) -> None:
    """No external networks returns empty list."""
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="192.168.1.10")},
        stacks={"app": "host1"},
    )
    compose_path = tmp_path / "app" / "compose.yaml"
    _write_compose(
        compose_path,
        {
            "services": {"app": {"image": "nginx"}},
            "networks": {"internal": {"driver": "bridge"}},
        },
    )

    networks = parse_external_networks(cfg, "app")
    assert networks == []


def test_parse_external_networks_no_networks_section(tmp_path: Path) -> None:
    """No networks section returns empty list."""
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="192.168.1.10")},
        stacks={"app": "host1"},
    )
    compose_path = tmp_path / "app" / "compose.yaml"
    _write_compose(
        compose_path,
        {"services": {"app": {"image": "nginx"}}},
    )

    networks = parse_external_networks(cfg, "app")
    assert networks == []


def test_parse_external_networks_missing_compose(tmp_path: Path) -> None:
    """Missing compose file returns empty list."""
    cfg = Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="192.168.1.10")},
        stacks={"app": "host1"},
    )
    # Don't create compose file

    networks = parse_external_networks(cfg, "app")
    assert networks == []


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

    def test_multiple_hosts_in_one_rule_with_or(self, tmp_path: Path) -> None:
        """Extracts multiple hosts from a single rule with || operator."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.web.rule": "Host(`app.example.com`) || Host(`app.backup.com`)",
                        "traefik.http.routers.web.entrypoints": "websecure",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://app.backup.com", "https://app.example.com"]

    def test_host_with_path_prefix(self, tmp_path: Path) -> None:
        """Extracts host from rule that includes PathPrefix."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.web.rule": "Host(`app.example.com`) && PathPrefix(`/api`)",
                        "traefik.http.routers.web.entrypoints": "websecure",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://app.example.com"]

    def test_multiple_services_in_stack(self, tmp_path: Path) -> None:
        """Extracts URLs from multiple services in one stack (like arr stack)."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "radarr": {
                    "image": "radarr",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.radarr.rule": "Host(`radarr.example.com`)",
                        "traefik.http.routers.radarr.entrypoints": "websecure",
                    },
                },
                "sonarr": {
                    "image": "sonarr",
                    "labels": {
                        "traefik.enable": "true",
                        "traefik.http.routers.sonarr.rule": "Host(`sonarr.example.com`)",
                        "traefik.http.routers.sonarr.entrypoints": "websecure",
                    },
                },
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://radarr.example.com", "https://sonarr.example.com"]

    def test_labels_in_list_format(self, tmp_path: Path) -> None:
        """Handles labels in list format (- key=value)."""
        stack_dir = tmp_path / "mystack"
        stack_dir.mkdir()
        compose_file = stack_dir / "compose.yaml"
        compose_data = {
            "services": {
                "web": {
                    "image": "nginx",
                    "labels": [
                        "traefik.enable=true",
                        "traefik.http.routers.web.rule=Host(`app.example.com`)",
                        "traefik.http.routers.web.entrypoints=websecure",
                    ],
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://app.example.com"]

    def test_no_entrypoints_defaults_to_http(self, tmp_path: Path) -> None:
        """When no entrypoints specified, defaults to http."""
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
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["http://app.example.com"]

    def test_multiple_entrypoints_with_websecure(self, tmp_path: Path) -> None:
        """When entrypoints includes websecure, use https."""
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
                        "traefik.http.routers.web.entrypoints": "web,websecure",
                    },
                }
            }
        }
        compose_file.write_text(yaml.dump(compose_data))

        config = self._create_config(tmp_path)
        urls = extract_website_urls(config, "mystack")
        assert urls == ["https://app.example.com"]
