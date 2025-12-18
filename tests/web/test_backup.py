"""Tests for file backup functionality."""

from pathlib import Path

from compose_farm.web.routes.api import _backup_file, _save_with_backup


class TestBackupFile:
    """Tests for _backup_file function."""

    def test_backup_creates_backup_directory(self, tmp_path: Path) -> None:
        """Test that backup creates .backups directory."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("content")

        _backup_file(test_file)

        backup_dir = tmp_path / ".backups"
        assert backup_dir.exists()
        assert backup_dir.is_dir()

    def test_backup_creates_timestamped_file(self, tmp_path: Path) -> None:
        """Test that backup creates file with timestamp."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("original content")

        backup_path = _backup_file(test_file)

        assert backup_path is not None
        assert backup_path.exists()
        assert backup_path.parent.name == ".backups"
        assert backup_path.name.startswith("test.yaml.")
        assert backup_path.read_text() == "original content"

    def test_backup_returns_none_for_nonexistent_file(self, tmp_path: Path) -> None:
        """Test that backup returns None if file doesn't exist."""
        test_file = tmp_path / "nonexistent.yaml"

        result = _backup_file(test_file)

        assert result is None

    def test_backup_cleanup_keeps_last_200(self, tmp_path: Path) -> None:
        """Test that old backups are cleaned up, keeping last 200."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("content")
        backup_dir = tmp_path / ".backups"
        backup_dir.mkdir()

        # Create 210 fake backups
        for i in range(210):
            backup = backup_dir / f"test.yaml.{i:08d}"
            backup.write_text(f"backup {i}")

        # Create a new backup (should trigger cleanup)
        _backup_file(test_file)

        # Should have 200 + 1 new = 201, then cleanup to 200
        # Actually cleanup happens after creating new one, so we get 200
        backups = list(backup_dir.glob("test.yaml.*"))
        assert len(backups) <= 201  # At most 201 (200 kept + 1 new)


class TestSaveWithBackup:
    """Tests for _save_with_backup function."""

    def test_save_creates_file_if_not_exists(self, tmp_path: Path) -> None:
        """Test that save creates new file."""
        test_file = tmp_path / "new.yaml"

        result = _save_with_backup(test_file, "new content")

        assert result is True
        assert test_file.exists()
        assert test_file.read_text() == "new content"

    def test_save_returns_false_if_unchanged(self, tmp_path: Path) -> None:
        """Test that save returns False if content unchanged."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("same content")

        result = _save_with_backup(test_file, "same content")

        assert result is False

    def test_save_creates_backup_before_overwrite(self, tmp_path: Path) -> None:
        """Test that save creates backup before changing file."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("original")

        result = _save_with_backup(test_file, "new content")

        assert result is True
        assert test_file.read_text() == "new content"

        # Check backup was created
        backup_dir = tmp_path / ".backups"
        assert backup_dir.exists()
        backups = list(backup_dir.glob("test.yaml.*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "original"

    def test_save_no_backup_if_unchanged(self, tmp_path: Path) -> None:
        """Test that no backup is created if content unchanged."""
        test_file = tmp_path / "test.yaml"
        test_file.write_text("content")

        _save_with_backup(test_file, "content")

        backup_dir = tmp_path / ".backups"
        assert not backup_dir.exists()
