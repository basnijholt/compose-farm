"""Tests for file backup functionality."""

from pathlib import Path

import pytest

from compose_farm.web.routes.api import _backup_file, _save_with_backup


@pytest.fixture
def xdg_backup_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set XDG_CONFIG_HOME to tmp_path and return the backup directory path."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "compose-farm" / "backups"


def test_backup_creates_timestamped_file(tmp_path: Path, xdg_backup_dir: Path) -> None:
    """Test that backup creates file in XDG backup dir with correct content."""
    test_file = tmp_path / "stacks" / "test.yaml"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("original content")

    backup_path = _backup_file(test_file)

    assert backup_path is not None
    assert backup_path.is_relative_to(xdg_backup_dir)
    assert backup_path.name.startswith("test.yaml.")
    assert backup_path.read_text() == "original content"


def test_backup_returns_none_for_nonexistent_file(tmp_path: Path, xdg_backup_dir: Path) -> None:
    """Test that backup returns None if file doesn't exist."""
    assert _backup_file(tmp_path / "nonexistent.yaml") is None


def test_save_creates_new_file(tmp_path: Path, xdg_backup_dir: Path) -> None:
    """Test that save creates new file without backup."""
    test_file = tmp_path / "new.yaml"

    assert _save_with_backup(test_file, "content") is True
    assert test_file.read_text() == "content"
    assert not xdg_backup_dir.exists()


def test_save_skips_unchanged_content(tmp_path: Path, xdg_backup_dir: Path) -> None:
    """Test that save returns False and creates no backup if unchanged."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("same")

    assert _save_with_backup(test_file, "same") is False
    assert not xdg_backup_dir.exists()


def test_save_creates_backup_before_overwrite(tmp_path: Path, xdg_backup_dir: Path) -> None:
    """Test that save backs up original before overwriting."""
    test_file = tmp_path / "stacks" / "test.yaml"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("original")

    assert _save_with_backup(test_file, "new") is True
    assert test_file.read_text() == "new"

    # Find backup in XDG dir
    backups = list(xdg_backup_dir.rglob("test.yaml.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "original"
