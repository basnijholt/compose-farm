"""Tests for state module."""

from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.state import (
    get_orphaned_stacks,
    get_stack_host,
    get_stacks_not_in_state,
    load_state,
    remove_stack,
    save_state,
    set_stack_host,
)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Create a config with a temporary config path for state storage."""
    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text("")  # Create empty file
    return Config(
        compose_dir=tmp_path / "compose",
        hosts={"nas01": Host(address="192.168.1.10")},
        stacks={"plex": "nas01"},
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


class TestGetStackHost:
    """Tests for get_stack_host function."""

    def test_get_existing_stack(self, config: Config) -> None:
        """Returns host for existing stack."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        host = get_stack_host(config, "plex")
        assert host == "nas01"

    def test_get_nonexistent_stack(self, config: Config) -> None:
        """Returns None for stack not in state."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        host = get_stack_host(config, "unknown")
        assert host is None


class TestSetStackHost:
    """Tests for set_stack_host function."""

    def test_set_new_stack(self, config: Config) -> None:
        """Adds new stack to state."""
        set_stack_host(config, "plex", "nas01")

        result = load_state(config)
        assert result["plex"] == "nas01"

    def test_update_existing_stack(self, config: Config) -> None:
        """Updates host for existing stack."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        set_stack_host(config, "plex", "nas02")

        result = load_state(config)
        assert result["plex"] == "nas02"


class TestRemoveStack:
    """Tests for remove_stack function."""

    def test_remove_existing_stack(self, config: Config) -> None:
        """Removes stack from state."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        remove_stack(config, "plex")

        result = load_state(config)
        assert "plex" not in result
        assert result["jellyfin"] == "nas02"

    def test_remove_nonexistent_stack(self, config: Config) -> None:
        """Removing nonexistent stack doesn't error."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        remove_stack(config, "unknown")  # Should not raise

        result = load_state(config)
        assert result["plex"] == "nas01"


class TestGetOrphanedStacks:
    """Tests for get_orphaned_stacks function."""

    def test_no_orphans(self, config: Config) -> None:
        """Returns empty dict when all stacks in state are in config."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        result = get_orphaned_stacks(config)
        assert result == {}

    def test_finds_orphaned_stack(self, config: Config) -> None:
        """Returns stacks in state but not in config."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        result = get_orphaned_stacks(config)
        # plex is in config, jellyfin is not
        assert result == {"jellyfin": "nas02"}

    def test_finds_orphaned_multi_host_stack(self, config: Config) -> None:
        """Returns multi-host orphaned stacks with host list."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  dozzle:\n  - nas01\n  - nas02\n")

        result = get_orphaned_stacks(config)
        assert result == {"dozzle": ["nas01", "nas02"]}

    def test_empty_state(self, config: Config) -> None:
        """Returns empty dict when state is empty."""
        result = get_orphaned_stacks(config)
        assert result == {}

    def test_all_orphaned(self, tmp_path: Path) -> None:
        """Returns all stacks when none are in config."""
        config_path = tmp_path / "compose-farm.yaml"
        config_path.write_text("")
        cfg = Config(
            compose_dir=tmp_path / "compose",
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={},  # No stacks in config
            config_path=config_path,
        )
        state_file = cfg.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        result = get_orphaned_stacks(cfg)
        assert result == {"plex": "nas01", "jellyfin": "nas02"}


class TestGetStacksNotInState:
    """Tests for get_stacks_not_in_state function."""

    def test_all_in_state(self, config: Config) -> None:
        """Returns empty list when all stacks are in state."""
        state_file = config.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        result = get_stacks_not_in_state(config)
        assert result == []

    def test_finds_missing_service(self, tmp_path: Path) -> None:
        """Returns stacks in config but not in state."""
        config_path = tmp_path / "compose-farm.yaml"
        config_path.write_text("")
        cfg = Config(
            compose_dir=tmp_path / "compose",
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={"plex": "nas01", "jellyfin": "nas01"},
            config_path=config_path,
        )
        state_file = cfg.get_state_path()
        state_file.write_text("deployed:\n  plex: nas01\n")

        result = get_stacks_not_in_state(cfg)
        assert result == ["jellyfin"]

    def test_empty_state(self, tmp_path: Path) -> None:
        """Returns all stacks when state is empty."""
        config_path = tmp_path / "compose-farm.yaml"
        config_path.write_text("")
        cfg = Config(
            compose_dir=tmp_path / "compose",
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={"plex": "nas01", "jellyfin": "nas01"},
            config_path=config_path,
        )

        result = get_stacks_not_in_state(cfg)
        assert set(result) == {"plex", "jellyfin"}

    def test_empty_config(self, config: Config) -> None:
        """Returns empty list when config has no stacks."""
        # config fixture has plex: nas01, but we need empty config
        config_path = config.config_path
        config_path.write_text("")
        cfg = Config(
            compose_dir=config.compose_dir,
            hosts={"nas01": Host(address="192.168.1.10")},
            stacks={},
            config_path=config_path,
        )

        result = get_stacks_not_in_state(cfg)
        assert result == []
