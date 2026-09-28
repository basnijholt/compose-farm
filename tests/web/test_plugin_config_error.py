"""A broken plugins section must not take down the web UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from compose_farm.web.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
