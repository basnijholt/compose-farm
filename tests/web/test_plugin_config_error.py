"""A broken plugins section must not take down the web UI."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from compose_farm.plugins import PluginError
from compose_farm.web.app import create_app
from compose_farm.web.deps import get_config

if TYPE_CHECKING:
    from pathlib import Path


def test_unknown_plugin_shows_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The editor with the error is shown instead of the app failing to start."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text(
        f"compose_dir: {compose_dir}\nhosts: {{h1: localhost}}\nstacks: {{}}\n"
        "plugins: {nope: null}\n"
    )
    monkeypatch.setenv("CF_CONFIG", str(config_path))

    with TestClient(
        create_app(), base_url="http://localhost", client=("127.0.0.1", 50000)
    ) as client:
        page = client.get("/")
        banner = client.get("/partials/config-error")

    assert page.status_code == 200
    assert "Unknown plugin(s): nope" in page.text
    assert "Unknown plugin(s): nope" in banner.text


def test_only_startup_installs_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An install inside a request would block the event loop, so requests only check."""
    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text(
        "hosts: {h1: localhost}\nstacks: {}\nplugins: {nope: null}\nplugin_packages: [pkg]\n"
    )
    monkeypatch.setenv("CF_CONFIG", str(config_path))
    monkeypatch.setattr("compose_farm.plugins._auto_install_results", {})

    with patch("compose_farm.plugins.install_packages") as install:
        with pytest.raises(PluginError, match="run `cf plugins install`"):
            get_config()
        install.assert_not_called()
        with pytest.raises(PluginError, match="don't provide them"):
            get_config(install_plugins=True)
        install.assert_called_once()
