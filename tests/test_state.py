"""Tests for state module."""

from pathlib import Path

import pytest

from compose_farm import state as state_module
from compose_farm.state import (
    get_service_host,
    load_state,
    remove_service,
    save_state,
    set_service_host,
)


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary state directory and patch _get_state_path."""
    state_path = tmp_path / ".config" / "compose-farm"
    state_path.mkdir(parents=True)

    def mock_get_state_path() -> Path:
        return state_path / "state.yaml"

    monkeypatch.setattr(state_module, "_get_state_path", mock_get_state_path)
    return state_path


class TestLoadState:
    """Tests for load_state function."""

    def test_load_state_empty(self, state_dir: Path) -> None:
        """Returns empty dict when state file doesn't exist."""
        _ = state_dir  # Fixture activates the mock
        result = load_state()
        assert result == {}

    def test_load_state_with_data(self, state_dir: Path) -> None:
        """Loads existing state from file."""
        state_file = state_dir / "state.yaml"
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        result = load_state()
        assert result == {"plex": "nas01", "jellyfin": "nas02"}

    def test_load_state_empty_file(self, state_dir: Path) -> None:
        """Returns empty dict for empty file."""
        state_file = state_dir / "state.yaml"
        state_file.write_text("")

        result = load_state()
        assert result == {}


class TestSaveState:
    """Tests for save_state function."""

    def test_save_state(self, state_dir: Path) -> None:
        """Saves state to file."""
        save_state({"plex": "nas01", "jellyfin": "nas02"})

        state_file = state_dir / "state.yaml"
        assert state_file.exists()
        content = state_file.read_text()
        assert "plex: nas01" in content
        assert "jellyfin: nas02" in content


class TestGetServiceHost:
    """Tests for get_service_host function."""

    def test_get_existing_service(self, state_dir: Path) -> None:
        """Returns host for existing service."""
        state_file = state_dir / "state.yaml"
        state_file.write_text("deployed:\n  plex: nas01\n")

        host = get_service_host("plex")
        assert host == "nas01"

    def test_get_nonexistent_service(self, state_dir: Path) -> None:
        """Returns None for service not in state."""
        state_file = state_dir / "state.yaml"
        state_file.write_text("deployed:\n  plex: nas01\n")

        host = get_service_host("unknown")
        assert host is None


class TestSetServiceHost:
    """Tests for set_service_host function."""

    def test_set_new_service(self, state_dir: Path) -> None:
        """Adds new service to state."""
        _ = state_dir  # Fixture activates the mock
        set_service_host("plex", "nas01")

        result = load_state()
        assert result["plex"] == "nas01"

    def test_update_existing_service(self, state_dir: Path) -> None:
        """Updates host for existing service."""
        state_file = state_dir / "state.yaml"
        state_file.write_text("deployed:\n  plex: nas01\n")

        set_service_host("plex", "nas02")

        result = load_state()
        assert result["plex"] == "nas02"


class TestRemoveService:
    """Tests for remove_service function."""

    def test_remove_existing_service(self, state_dir: Path) -> None:
        """Removes service from state."""
        state_file = state_dir / "state.yaml"
        state_file.write_text("deployed:\n  plex: nas01\n  jellyfin: nas02\n")

        remove_service("plex")

        result = load_state()
        assert "plex" not in result
        assert result["jellyfin"] == "nas02"

    def test_remove_nonexistent_service(self, state_dir: Path) -> None:
        """Removing nonexistent service doesn't error."""
        state_file = state_dir / "state.yaml"
        state_file.write_text("deployed:\n  plex: nas01\n")

        remove_service("unknown")  # Should not raise

        result = load_state()
        assert result["plex"] == "nas01"
