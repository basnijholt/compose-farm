"""Tests for config command module."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from compose_farm.cli import app
from compose_farm.cli.config import (
    _detect_domain,
    _generate_template,
    _get_config_file,
    _get_editor,
)
from compose_farm.config import Config, Host


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


class TestDetectDomain:
    """Tests for _detect_domain function."""

    def test_returns_none_for_empty_stacks(self) -> None:
        cfg = Config(
            compose_dir=Path("/opt/compose"),
            hosts={"nas": Host(address="192.168.1.6")},
            stacks={},
        )
        result = _detect_domain(cfg)
        assert result is None

    def test_skips_local_domains(self, tmp_path: Path) -> None:
        # Create a minimal compose file with .local domain
        stack_dir = tmp_path / "test"
        stack_dir.mkdir()
        compose = stack_dir / "compose.yaml"
        compose.write_text(
            """
name: test
services:
  web:
    image: nginx
    labels:
      - "traefik.http.routers.test-local.rule=Host(`test.local`)"
"""
        )
        cfg = Config(
            compose_dir=tmp_path,
            hosts={"nas": Host(address="192.168.1.6")},
            stacks={"test": "nas"},
        )
        result = _detect_domain(cfg)
        # .local should be skipped
        assert result is None


def _env_value(content: str, key: str) -> str:
    """The value of an uncommented KEY=value line."""
    values = [line.split("=", 1)[1] for line in content.splitlines() if line.startswith(f"{key}=")]
    assert len(values) == 1, content
    return values[0]


class TestConfigInitEnv:
    """Tests for cf config init-env command."""

    def test_init_env_creates_file(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file)]
        )

        assert result.exit_code == 0
        assert env_file.exists()
        content = env_file.read_text()
        assert "CF_COMPOSE_DIR=/opt/compose" in content
        assert "CF_UID=" in content
        assert "CF_GID=" in content
        assert "# CF_WEB_USERNAME=admin" in content.splitlines()
        password = _env_value(content, "CF_WEB_PASSWORD")
        assert len(password) >= 32
        assert f"Web UI login: admin / {password}" in result.stdout

    def test_init_env_generates_a_new_password_each_time(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        passwords = set()
        for name in ("a.env", "b.env"):
            env_file = tmp_path / name
            runner.invoke(app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file)])
            passwords.add(_env_value(env_file.read_text(), "CF_WEB_PASSWORD"))
        assert len(passwords) == 2

    def test_init_env_force_keeps_existing_password(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text("OLD=1\nCF_WEB_PASSWORD=keep[me]\n")

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0
        assert _env_value(env_file.read_text(), "CF_WEB_PASSWORD") == "keep[me]"
        assert "CF_WEB_PASSWORD: kept from existing .env" in result.stdout
        assert "keep[me]" not in result.output

    def test_init_env_force_overwrites(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text("OLD_CONTENT=true")

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0
        content = env_file.read_text()
        assert "OLD_CONTENT" not in content
        assert "CF_COMPOSE_DIR" in content

    @pytest.mark.parametrize("existing", [True, False])
    def test_init_env_file_is_private(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        existing: bool,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        if existing:
            env_file.write_text("OLD=1\n")
            env_file.chmod(0o644)

        runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert env_file.stat().st_mode & 0o777 == 0o600

    def test_init_env_keeps_the_definition_compose_uses(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The last definition wins; export, quotes, and comments are kept as written."""
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text('CF_WEB_PASSWORD=first\nexport CF_WEB_PASSWORD="second one" # note\n')

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        content = env_file.read_text()
        assert 'export CF_WEB_PASSWORD="second one" # note' in content.splitlines()
        assert "first" not in content
        assert "kept from existing .env" in result.stdout

    def test_init_env_replaces_an_empty_password(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text('CF_WEB_PASSWORD=""\n')

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        password = _env_value(env_file.read_text(), "CF_WEB_PASSWORD")
        assert len(password) == 64
        assert f"Web UI login: admin / {password}" in result.stdout

    def test_init_env_survives_a_line_the_pattern_misses(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """python-dotenv accepts a quoted key; without a matching line, generate a password."""
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text("'CF_WEB_PASSWORD'=x\n")

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0, result.output
        password = _env_value(env_file.read_text(), "CF_WEB_PASSWORD")
        assert re.fullmatch(r"[0-9a-f]{64}", password)
        assert f"Web UI login: admin / {password}" in result.stdout

    def test_init_env_force_keeps_existing_username(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text("CF_WEB_USERNAME=bob\nCF_WEB_PASSWORD=keep\n")

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0, result.output
        content = env_file.read_text()
        assert _env_value(content, "CF_WEB_USERNAME") == "bob"
        assert "# CF_WEB_USERNAME=admin" not in content
        assert _env_value(content, "CF_WEB_PASSWORD") == "keep"
        assert "CF_WEB_PASSWORD: kept from existing .env" in result.stdout

    def test_init_env_prints_the_kept_username_with_a_new_password(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The definition is kept verbatim; the login shows the parsed username."""
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text('CF_WEB_USERNAME=admin\nexport CF_WEB_USERNAME="bob" # me\n')

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0, result.output
        content = env_file.read_text()
        assert 'export CF_WEB_USERNAME="bob" # me' in content.splitlines()
        assert "CF_WEB_USERNAME=admin" not in content
        password = _env_value(content, "CF_WEB_PASSWORD")
        assert re.fullmatch(r"[0-9a-f]{64}", password)
        assert f"Web UI login: bob / {password}" in result.stdout

    def test_init_env_warns_about_shell_overrides(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        monkeypatch.setenv("CF_WEB_PASSWORD", "from-shell")
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(tmp_path / ".env")]
        )

        assert "CF_WEB_PASSWORD is set in your shell" in result.output

    def test_init_env_prints_a_kept_username_verbatim(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text("CF_WEB_USERNAME=[b]ob:smile:\n")

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0
        password = _env_value(env_file.read_text(), "CF_WEB_PASSWORD")
        assert f"Web UI login: [b]ob:smile: / {password}" in result.stdout

    @pytest.mark.parametrize(
        "existing",
        [
            "CF_WEB_USERNAME=alice\n'CF_WEB_USERNAME'=bob\n",  # dotenv's last one: a quoted key Compose rejects
            "SOME_KEY=bob\nCF_WEB_USERNAME=${SOME_KEY}\n",  # the rewrite drops SOME_KEY
            "CF_WEB_USERNAME=bob\nCF_WEB_USERNAME=\n",  # the last definition is empty
        ],
    )
    def test_init_env_prints_the_username_it_saves(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        existing: str,
    ) -> None:
        """A definition Compose couldn't use as written falls back to the default login."""
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text(existing)

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0
        content = env_file.read_text()
        assert "# CF_WEB_USERNAME=admin" in content.splitlines()
        assert f"Web UI login: admin / {_env_value(content, 'CF_WEB_PASSWORD')}" in result.stdout

    def test_init_env_keeps_a_multiline_password_intact(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text('CF_WEB_PASSWORD="abc\ndef"\n')

        result = runner.invoke(
            app, ["config", "init-env", "-p", str(config_file), "-o", str(env_file), "-f"]
        )

        assert result.exit_code == 0
        assert 'CF_WEB_PASSWORD="abc\ndef"\n' in env_file.read_text()

    def test_init_env_prompts_on_existing(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))
        env_file = tmp_path / ".env"
        env_file.write_text("KEEP_THIS=true")

        result = runner.invoke(
            app,
            ["config", "init-env", "-p", str(config_file), "-o", str(env_file)],
            input="n\n",
        )

        assert result.exit_code == 0
        assert "Aborted" in result.stdout
        assert env_file.read_text() == "KEEP_THIS=true"

    def test_init_env_defaults_to_current_dir(
        self,
        runner: CliRunner,
        tmp_path: Path,
        valid_config_data: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CF_CONFIG", raising=False)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "compose-farm.yaml"
        config_file.write_text(yaml.dump(valid_config_data))

        # Create a separate working directory
        work_dir = tmp_path / "workdir"
        work_dir.mkdir()
        monkeypatch.chdir(work_dir)

        result = runner.invoke(app, ["config", "init-env", "-p", str(config_file)])

        assert result.exit_code == 0
        # Should create .env in current directory, not config directory
        env_file = work_dir / ".env"
        assert env_file.exists()
        assert not (config_dir / ".env").exists()
