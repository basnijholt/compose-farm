"""Tests for the web CLI command."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from typer.testing import CliRunner

from compose_farm.cli import app
from compose_farm.cli.web import _compose_farm_banner


def test_compose_farm_banner_is_aligned() -> None:
    """Banner contains the expected aligned ASCII art."""
    assert _compose_farm_banner().plain == (
        "           .-^-.\n"
        "        .-'  _  '-.\n"
        "       /    |_|    \\\n"
        "      /-------------\\\n"
        "     /   []  []  []   \\\n"
        "    /__________________\\\n"
        "    |   COMPOSE FARM   |\n"
        "    |__________________|"
    )


def test_web_command_prints_banner_and_starts_uvicorn(monkeypatch: Any) -> None:
    """Web prints startup logs before delegating to uvicorn."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))

    result = CliRunner().invoke(app, ["web", "--host", "127.0.0.1", "--port", "9999"])

    assert result.exit_code == 0
    assert "COMPOSE FARM" in result.output
    assert "Starting Compose Farm Web UI" in result.output
    assert calls == [
        (
            ("compose_farm.web:create_app",),
            {
                "factory": True,
                "host": "127.0.0.1",
                "port": 9999,
                "reload": False,
                "log_level": "info",
            },
        )
    ]
