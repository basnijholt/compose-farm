"""Tests for config command module."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from compose_farm.cli import app
from compose_farm.cli.config import (
    _generate_template,
    _get_config_file,
    _get_editor,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def valid_config_data() -> dict[str, Any]:
    return {
        "compose_dir": "/opt/compose",
        "hosts": {"server1": "192.168.1.10"},
        "stacks": {"nginx": "server1"},
    }


class TestGetEditor:
    """Tests for _get_editor function."""

    def test_uses_editor_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EDITOR", "code")
        monkeypatch.delenv("VISUAL", raising=False)
        assert _get_editor() == "code"

    def test_uses_visual_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setenv("VISUAL", "subl")
        assert _get_editor() == "subl"

    def test_editor_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EDITOR", "vim")
        monkeypatch.setenv("VISUAL", "code")
        assert _get_editor() == "vim"


class TestGetConfigFile:
    """Tests for _get_config_file function."""

    def test_explicit_path(self, tmp_path: Path) -> None:
        config_file = tmp_path / "my-config.yaml"
        config_file.touch()
        result = _get_config_file(config_file)
        assert result == config_file.resolve()

    def test_cf_config_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_file = tmp_path / "env-config.yaml"
        config_file.touch()
        monkeypatch.setenv("CF_CONFIG", str(config_file))
        result = _get_config_file(None)
        assert result == config_file.resolve()

    def test_returns_none_when_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CF_CONFIG", raising=False)
        # Set XDG_CONFIG_HOME to a nonexistent path - config_search_paths() will
        # now return paths that don't exist
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
        result = _get_config_file(None)
        assert result is None


class TestGenerateTemplate:
    """Tests for _generate_template function."""

    def test_generates_valid_yaml(self) -> None:
        template = _generate_template()
        # Should be valid YAML
        data = yaml.safe_load(template)
        assert "compose_dir" in data
        assert "hosts" in data
        assert "stacks" in data

    def test_has_documentation_comments(self) -> None:
        template = _generate_template()
        assert "# Compose Farm configuration" in template
        assert "hosts:" in template
        assert "stacks:" in template


class TestConfigInit:
    """Tests for cf config init command."""

    def test_init_creates_file(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "new-config.yaml"
        result = runner.invoke(app, ["config", "init", "-p", str(config_file)])
        assert result.exit_code == 0
        assert config_file.exists()
        assert "Config file created" in result.stdout

    def test_init_force_overwrites(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "existing.yaml"
        config_file.write_text("old content")
        result = runner.invoke(app, ["config", "init", "-p", str(config_file), "-f"])
        assert result.exit_code == 0
        content = config_file.read_text()
        assert "old content" not in content
        assert "compose_dir" in content

    def test_init_prompts_on_existing(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "existing.yaml"
        config_file.write_text("old content")
        result = runner.invoke(app, ["config", "init", "-p", str(config_file)], input="n\n")
        assert result.exit_code == 0
        assert "Aborted" in result.stdout
        assert config_file.read_text() == "old content"


class TestConfigPath:
    """Tests for cf config path command."""

    def test_path_shows_config(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        result = runner.invoke(app, ["config", "path"])
        assert result.exit_code == 0
        assert str(config_file) in result.stdout

    def test_path_with_explicit_path(self, runner: CliRunner, tmp_path: Path) -> None:
        # When explicitly provided, path is returned even if file doesn't exist
        nonexistent = tmp_path / "nonexistent.yaml"
        result = runner.invoke(app, ["config", "path", "-p", str(nonexistent)])
        assert result.exit_code == 0
        assert str(nonexistent) in result.stdout


class TestConfigShow:
    """Tests for cf config show command."""

    def test_show_displays_content(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "Config file:" in result.stdout

    def test_show_raw_output(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        content = yaml.dump(valid_config_data)
        config_file.write_text(content)
        result = runner.invoke(app, ["config", "show", "-r"])
        assert result.exit_code == 0
        assert content in result.stdout


class TestConfigValidate:
    """Tests for cf config validate command."""

    def test_validate_valid_config(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        result = runner.invoke(app, ["config", "validate"])
        assert result.exit_code == 0
        assert "Valid config" in result.stdout
        assert "Hosts: 1" in result.stdout
        assert "Stacks: 1" in result.stdout

    def test_validate_invalid_config(self, runner: CliRunner, tmp_path: Path) -> None:
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: [yaml: content")
        result = runner.invoke(app, ["config", "validate", "-p", str(config_file)])
        assert result.exit_code == 1
        # Error goes to stderr (captured in output when using CliRunner)
        output = result.stdout + (result.stderr or "")
        assert "Invalid config" in output or "✗" in output

    def test_validate_missing_config(self, runner: CliRunner, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent.yaml"
        result = runner.invoke(app, ["config", "validate", "-p", str(nonexistent)])
        assert result.exit_code == 1
        # Error goes to stderr
        output = result.stdout + (result.stderr or "")
        assert "Config file not found" in output or "not found" in output.lower()
