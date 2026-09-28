"""Tests for the example agenix plugin in examples/plugins/agenix."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from compose_farm.plugins import HookContext, PluginError
from tests.plugin_helpers import load_example_plugin, make_config, use_plugins

if TYPE_CHECKING:
    from compose_farm.config import Config

agenix = load_example_plugin("agenix")
STACKS: dict[str, str | list[str]] = {"mealie": "h1", "forgejo": "h1", "plain": "h1"}


def _setup(tmp_path: Path, options: dict[str, Any]) -> Config:
    plugin = agenix.AgenixPlugin(options)
    plugin.name = "agenix"
    return use_plugins(make_config(tmp_path, STACKS), plugin)


class TestOptions:
    """Option validation."""

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({}, "stacks is required"),
            ({"stacks": {}, "nope": 1}, "unknown option"),
            ({"stacks": {}, "secrets_dir": 5}, "secrets_dir must be a string"),
            ({"stacks": {}, "secrets_dir": "run/agenix"}, "secrets_dir must be an absolute path"),
            ({"stacks": ["mealie"]}, "stacks must map stack names"),
            ({"stacks": {"mealie": 5}}, "stacks.mealie"),
            ({"stacks": {"mealie": {"env": "a.env", "oops": "b"}}}, "unknown key"),
            ({"stacks": {"mealie": {"files": [1]}}}, "stacks.mealie.files"),
        ],
    )
    def test_invalid(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            agenix.AgenixPlugin(options)


class TestComposeArgs:
    """Env files become --env-file arguments, keeping the stack's own .env."""

    def test_keeps_dotenv_when_present(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"stacks": {"mealie": "mealie.env"}})
        (cfg.get_stack_dir("mealie") / ".env").write_text("DOMAIN=example.com\n")
        assert cfg.compose_args("mealie", "h1") == [
            "--env-file",
            ".env",
            "--env-file",
            "/run/agenix/mealie.env",
        ]

    def test_without_dotenv(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"stacks": {"forgejo": ["forgejo.env", "/secrets/db.env"]}})
        assert cfg.compose_args("forgejo", "h1") == [
            "--env-file",
            "/run/agenix/forgejo.env",
            "--env-file",
            "/secrets/db.env",
        ]

    def test_unlisted_stack_is_untouched(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"stacks": {"mealie": "mealie.env"}})
        assert cfg.compose_args("plain", "h1") == []

    def test_files_only_adds_no_args(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"stacks": {"forgejo": {"files": ["forgejo-db-password"]}}})
        assert cfg.compose_args("forgejo", "h1") == []

    def test_custom_secrets_dir(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"secrets_dir": "/run/secrets", "stacks": {"mealie": "m.env"}})
        assert cfg.compose_args("mealie", "h1") == ["--env-file", "/run/secrets/m.env"]


class TestPreflight:
    """Every configured secret must be readable on the host."""

    async def test_reports_missing_secrets(self, tmp_path: Path) -> None:
        secrets = tmp_path / "agenix"
        secrets.mkdir()
        (secrets / "mealie.env").write_text("TOKEN=x\n")
        cfg = _setup(
            tmp_path,
            {
                "secrets_dir": str(secrets),
                "stacks": {
                    "mealie": "mealie.env",
                    "forgejo": {"env": "forgejo.env", "files": ["db password"]},
                },
            },
        )
        plugin = cfg.get_plugins()[0]
        assert await plugin.preflight(HookContext(cfg, "mealie", "h1")) == []
        assert await plugin.preflight(HookContext(cfg, "forgejo", "h1")) == [
            f"secret {secrets}/forgejo.env is missing or unreadable",
            f"secret {secrets}/db password is missing or unreadable",
        ]

    async def test_unlisted_stack_runs_nothing(self, tmp_path: Path) -> None:
        cfg = _setup(tmp_path, {"stacks": {"mealie": "mealie.env"}})
        cfg.hosts.clear()  # Any command would fail with "host not in config"
        assert await cfg.get_plugins()[0].preflight(HookContext(cfg, "plain", "h1")) == []


def test_package_registers_entry_point() -> None:
    pyproject = Path(__file__).parent.parent / "examples" / "plugins" / "agenix" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["entry-points"]["compose_farm.plugins"] == {
        "agenix": "compose_farm_agenix:AgenixPlugin"
    }
