"""Tests for the example pin plugin in examples/plugins/pin."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from compose_farm.plugins import HookContext, PluginError
from tests.plugin_helpers import load_example_plugin, make_config

pin = load_example_plugin("pin")


def _ctx(tmp_path: Path, stack: str, host: str, source: str | None = None) -> HookContext:
    cfg = make_config(tmp_path, {stack: host}, hosts=("nas", "nuc", "hp"))
    return HookContext(cfg, stack, host, source)


class TestOptions:
    """Option validation."""

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({"gitea": 5}, "gitea: must be a host name"),
            ({"gitea": {"reason": "x"}}, "gitea: host is required"),
            ({"gitea": {"host": "nas", "why": "x"}}, "gitea: unknown key"),
            ({"gitea": {"host": "nas", "reason": 5}}, "gitea: reason must be a string"),
            ({"gitea": {"host": [1]}}, "gitea: must be a host name"),
        ],
    )
    def test_invalid(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            pin.PinPlugin(options)


class TestPin:
    """Pinned stacks only start on their host."""

    async def test_start_on_pinned_host(self, tmp_path: Path) -> None:
        plugin = pin.PinPlugin({"gitea": "nas"})
        ctx = _ctx(tmp_path, "gitea", "nas")
        await plugin.before_up(ctx)
        assert await plugin.preflight(ctx) == []

    async def test_refuses_another_host_with_reason(self, tmp_path: Path) -> None:
        plugin = pin.PinPlugin({"gitea": {"host": "nas", "reason": "router forwards ports to it"}})
        ctx = _ctx(tmp_path, "gitea", "nuc", source="nas")
        message = "gitea is pinned to nas (router forwards ports to it); not starting it on nuc"
        with pytest.raises(PluginError, match=re.escape(message)):
            await plugin.before_up(ctx)
        assert await plugin.preflight(ctx) == [message]

    async def test_several_allowed_hosts(self, tmp_path: Path) -> None:
        plugin = pin.PinPlugin({"frigate": {"host": ["nas", "hp"]}})
        await plugin.before_up(_ctx(tmp_path, "frigate", "hp"))
        with pytest.raises(PluginError, match="pinned to nas, hp"):
            await plugin.before_up(_ctx(tmp_path, "frigate", "nuc"))

    async def test_other_stacks_are_ignored(self, tmp_path: Path) -> None:
        plugin = pin.PinPlugin({"gitea": "nas"})
        ctx = _ctx(tmp_path, "mealie", "nuc", source="nas")
        await plugin.before_up(ctx)
        assert await plugin.preflight(ctx) == []


def test_package_registers_entry_point() -> None:
    pyproject = Path(__file__).parent.parent / "examples" / "plugins" / "pin" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["entry-points"]["compose_farm.plugins"] == {
        "pin": "compose_farm_pin:PinPlugin"
    }
