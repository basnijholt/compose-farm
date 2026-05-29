"""Tests for plugin CLI command registration."""

from __future__ import annotations

from typing import Any

import pytest
import typer

from compose_farm.plugins import HookExecutionError
from compose_farm.plugins.manager import register_cli_commands


class _EntryPoint:
    """Small entry-point test double."""

    def __init__(self, name: str, loaded_obj: Any) -> None:
        self.name = name
        self._loaded_obj = loaded_obj

    def load(self) -> Any:
        return self._loaded_obj


class _ConflictPlugin:
    def register_cli_commands(self, registrar: Any) -> None:
        @registrar.command("up")
        def _up() -> None:
            return None


class _LabPlugin:
    def register_cli_commands(self, registrar: Any) -> None:
        lab_app = typer.Typer()

        @lab_app.command("ports")
        def _ports() -> None:
            return None

        registrar.add_typer(lab_app, name="lab")


def test_register_cli_commands_rejects_name_collision() -> None:
    app = typer.Typer()

    @app.command("up")
    def up_cmd() -> None:
        return None

    ep = _EntryPoint("conflict", _ConflictPlugin)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "compose_farm.plugins.manager.entry_points",
            lambda *, group: [ep] if group == "compose_farm.plugins" else [],
        )
        with pytest.raises(HookExecutionError, match="cannot register CLI name"):
            register_cli_commands(app)


def test_register_cli_commands_adds_plugin_group() -> None:
    app = typer.Typer()
    ep = _EntryPoint("lab", _LabPlugin)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "compose_farm.plugins.manager.entry_points",
            lambda *, group: [ep] if group == "compose_farm.plugins" else [],
        )
        loaded = register_cli_commands(app)

    assert loaded == ("lab",)
    group_names = [group.name for group in app.registered_groups]
    assert "lab" in group_names
