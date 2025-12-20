"""Tests for config module."""

from pathlib import Path

import pytest
import yaml

from compose_farm.config import Config, Host, load_config


class TestHost:
    """Tests for Host model."""

    def test_host_with_all_fields(self) -> None:
        host = Host(address="192.168.1.10", user="docker", port=2222)
        assert host.address == "192.168.1.10"
        assert host.user == "docker"
        assert host.port == 2222

    def test_host_defaults(self) -> None:
        host = Host(address="192.168.1.10")
        assert host.address == "192.168.1.10"
        assert host.port == 22
        # user defaults to current user, just check it's set
        assert host.user

    def test_local_host(self) -> None:
        host = Host(address="local")
        assert host.address == "local"


class TestConfig:
    """Tests for Config model."""

    def test_config_validation(self) -> None:
        config = Config(
            compose_dir=Path("/opt/compose"),
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={"plex": "nas01"},
        )
        assert config.compose_dir == Path("/opt/compose")
        assert "nas01" in config.hosts
        assert config.stacks["plex"] == "nas01"

    def test_config_invalid_stack_host(self) -> None:
        with pytest.raises(ValueError, match="unknown host"):
            Config(
                compose_dir=Path("/opt/compose"),
                hosts={"nas01": Host(address="192.168.1.10")},
                stacks={"plex": "nonexistent"},
            )

    def test_get_host(self) -> None:
        config = Config(
            compose_dir=Path("/opt/compose"),
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={"plex": "nas01"},
        )
        host = config.get_host("plex")
        assert host.address == "192.168.1.10"

    def test_get_host_unknown_stack(self) -> None:
        config = Config(
            compose_dir=Path("/opt/compose"),
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={"plex": "nas01"},
        )
        with pytest.raises(ValueError, match="Unknown stack"):
            config.get_host("unknown")

    def test_get_compose_path(self) -> None:
        config = Config(
            compose_dir=Path("/opt/compose"),
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={"plex": "nas01"},
        )
        path = config.get_compose_path("plex")
        # Defaults to compose.yaml when no file exists
        assert path == Path("/opt/compose/plex/compose.yaml")


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_full_host_format(self, tmp_path: Path) -> None:
        config_data = {
            "compose_dir": "/opt/compose",
            "hosts": {
                "nas01": {"address": "192.168.1.10", "user": "docker", "port": 2222},
            },
            "stacks": {"plex": "nas01"},
        }
        config_file = tmp_path / "sdc.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.hosts["nas01"].address == "192.168.1.10"
        assert config.hosts["nas01"].user == "docker"
        assert config.hosts["nas01"].port == 2222

    def test_load_config_simple_host_format(self, tmp_path: Path) -> None:
        config_data = {
            "compose_dir": "/opt/compose",
            "hosts": {"nas01": "192.168.1.10"},
            "stacks": {"plex": "nas01"},
        }
        config_file = tmp_path / "sdc.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.hosts["nas01"].address == "192.168.1.10"

    def test_load_config_mixed_host_formats(self, tmp_path: Path) -> None:
        config_data = {
            "compose_dir": "/opt/compose",
            "hosts": {
                "nas01": {"address": "192.168.1.10", "user": "docker"},
                "nas02": "192.168.1.11",
            },
            "stacks": {"plex": "nas01", "jellyfin": "nas02"},
        }
        config_file = tmp_path / "sdc.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.hosts["nas01"].user == "docker"
        assert config.hosts["nas02"].address == "192.168.1.11"

    def test_load_config_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CF_CONFIG", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_config"))
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config()

    def test_load_config_local_host(self, tmp_path: Path) -> None:
        config_data = {
            "compose_dir": "/opt/compose",
            "hosts": {"local": "localhost"},
            "stacks": {"test": "local"},
        }
        config_file = tmp_path / "sdc.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.hosts["local"].address == "localhost"
