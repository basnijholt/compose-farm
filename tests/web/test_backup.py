"""Tests for file backup functionality."""

from pathlib import Path

from compose_farm.web.routes.api import _backup_file, _save_with_backup


def test_backup_creates_timestamped_file(tmp_path: Path) -> None:
    """Test that backup creates file in .backups with correct content."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("original content")

    backup_path = _backup_file(test_file)

    assert backup_path is not None
    assert backup_path.parent.name == ".backups"
    assert backup_path.name.startswith("test.yaml.")
    assert backup_path.read_text() == "original content"


def test_backup_returns_none_for_nonexistent_file(tmp_path: Path) -> None:
    """Test that backup returns None if file doesn't exist."""
    assert _backup_file(tmp_path / "nonexistent.yaml") is None


def test_save_creates_new_file(tmp_path: Path) -> None:
    """Test that save creates new file without backup."""
    test_file = tmp_path / "new.yaml"

    assert _save_with_backup(test_file, "content") is True
    assert test_file.read_text() == "content"
    assert not (tmp_path / ".backups").exists()


def test_save_skips_unchanged_content(tmp_path: Path) -> None:
    """Test that save returns False and creates no backup if unchanged."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("same")

    assert _save_with_backup(test_file, "same") is False
    assert not (tmp_path / ".backups").exists()


def test_save_creates_backup_before_overwrite(tmp_path: Path) -> None:
    """Test that save backs up original before overwriting."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("original")

    assert _save_with_backup(test_file, "new") is True
    assert test_file.read_text() == "new"

    backups = list((tmp_path / ".backups").glob("test.yaml.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "original"
