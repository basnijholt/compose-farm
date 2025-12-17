"""Tests for state module."""

from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.state import (
    get_orphaned_services,
    get_service_host,
    get_services_not_in_state,
    load_state,
    remove_service,
    save_state,
    set_service_host,
)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Create a config with a temporary config path for state storage."""
    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text("")  # Create empty file
    return Config(
        compose_dir=tmp_path / "compose",
        hosts={"nas01": Host(address="192.168.1.10")},
        services={"plex": "nas01"},
        config_path=config_path,
    )


class TestLoadState:
    """Tests for load_state function."""

    def test_load_state_empty(self, config: Config) -> None:
        """Returns empty dict when state file doesn't exist."""
        result = load_state(config)
        assert result == {}

    def test_load_state_with_data(self, config: Config) -> None:
        """Loads existing state from file."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        result = load_state(config)
        assert result == {"plex": "nas01", "jellyfin": "nas02"}

    def test_load_state_empty_file(self, config: Config) -> None:
        """Returns empty dict for empty file."""
        state_file = config.get_state_path()
        state_file.write_text("")

        result = load_state(config)
        assert result == {}


class TestSaveState:
    """Tests for save_state function."""

    def test_save_state(self, config: Config) -> None:
        """Saves state to file."""
        save_state(config, {"plex": "nas01", "jellyfin": "nas02"})

        state_file = config.get_state_path()
        assert state_file.exists()
        content = state_file.read_text()
        assert "plex: nas01" in content
        assert "jellyfin: nas02" in content


class TestGetServiceHost:
    """Tests for get_service_host function."""

    def test_get_existing_service(self, config: Config) -> None:
        """Returns host for existing service."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        host = get_service_host(config, "plex")
        assert host == "nas01"

    def test_get_nonexistent_service(self, config: Config) -> None:
        """Returns None for service not in state."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        host = get_service_host(config, "unknown")
        assert host is None


class TestSetServiceHost:
    """Tests for set_service_host function."""

    def test_set_new_service(self, config: Config) -> None:
        """Adds new service to state."""
        set_service_host(config, "plex", "nas01")

        result = load_state(config)
        assert result["plex"] == "nas01"

    def test_update_existing_service(self, config: Config) -> None:
        """Updates host for existing service."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        set_service_host(config, "plex", "nas02")

        result = load_state(config)
        assert result["plex"] == "nas02"


class TestRemoveService:
    """Tests for remove_service function."""

    def test_remove_existing_service(self, config: Config) -> None:
        """Removes service from state."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        remove_service(config, "plex")

        result = load_state(config)
        assert "plex" not in result
        assert result["jellyfin"] == "nas02"

    def test_remove_nonexistent_service(self, config: Config) -> None:
        """Removing nonexistent service doesn't error."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        remove_service(config, "unknown")  # Should not raise

        result = load_state(config)
        assert result["plex"] == "nas01"


class TestGetOrphanedServices:
    """Tests for get_orphaned_services function."""

    def test_no_orphans(self, config: Config) -> None:
        """Returns empty dict when all services in state are in config."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        result = get_orphaned_services(config)
        assert result == {}

    def test_finds_orphaned_service(self, config: Config) -> None:
        """Returns services in state but not in config."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        result = get_orphaned_services(config)
        # plex is in config, jellyfin is not
        assert result == {"jellyfin": "nas02"}

    def test_finds_orphaned_multi_host_service(self, config: Config) -> None:
        """Returns multi-host orphaned services with host list."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  dozzle:\n  - nas01\n  - nas02\n")

        result = get_orphaned_services(config)
        assert result == {"dozzle": ["nas01", "nas02"]}

    def test_empty_state(self, config: Config) -> None:
        """Returns empty dict when state is empty."""
        result = get_orphaned_services(config)
        assert result == {}

    def test_all_orphaned(self, tmp_path: Path) -> None:
        """Returns all services when none are in config."""
        config_path = tmp_path / "compose-farm.yaml"
        config_path.write_text("")
        cfg = Config(
            compose_dir=tmp_path / "compose",
            hosts={"nas01": Host(address="192.168.1.10")},
            services={},  # No services in config
            config_path=config_path,
        )
        state_file = cfg.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        result = get_orphaned_services(cfg)
        assert result == {"plex": "nas01", "jellyfin": "nas02"}


class TestGetServicesNotInState:
    """Tests for get_services_not_in_state function."""

    def test_all_in_state(self, config: Config) -> None:
        """Returns empty list when all services are in state."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        result = get_services_not_in_state(config)
        assert result == []

    def test_finds_missing_service(self, tmp_path: Path) -> None:
        """Returns services in config but not in state."""
        config_path = tmp_path / "compose-farm.yaml"
        config_path.write_text("")
        cfg = Config(
            compose_dir=tmp_path / "compose",
            hosts={"nas01": Host(address="192.168.1.10")},
            services={"plex": "nas01", "jellyfin": "nas01"},
            config_path=config_path,
        )
        state_file = cfg.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        result = get_services_not_in_state(cfg)
        assert result == ["jellyfin"]

    def test_empty_state(self, tmp_path: Path) -> None:
        """Returns all services when state is empty."""
        config_path = tmp_path / "compose-farm.yaml"
        config_path.write_text("")
        cfg = Config(
            compose_dir=tmp_path / "compose",
            hosts={"nas01": Host(address="192.168.1.10")},
            services={"plex": "nas01", "jellyfin": "nas01"},
            config_path=config_path,
        )

        result = get_services_not_in_state(cfg)
        assert set(result) == {"plex", "jellyfin"}

    def test_empty_config(self, config: Config) -> None:
        """Returns empty list when config has no services."""
        # config fixture has plex: nas01, but we need empty config
        config_path = config.config_path
        config_path.write_text("")
        cfg = Config(
            compose_dir=config.compose_dir,
            hosts={"nas01": Host(address="192.168.1.10")},
            services={},
            config_path=config_path,
        )

        result = get_services_not_in_state(cfg)
        assert result == []
