"""Tests for the example traefik-policy plugin in examples/plugins/traefik-policy."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from compose_farm.plugins import HookContext, PluginError
from tests.plugin_helpers import load_example_plugin, make_config

policy = load_example_plugin("traefik_policy", folder="traefik-policy")

COMPOSE = """\
services:
  web:
    image: nginx
    ports: ["8080:80"]
    labels:
      - traefik.enable=true
      - traefik.http.routers.web.rule=Host(`web.${DOMAIN}`)
      - traefik.http.routers.web.entrypoints=websecure,wan
      - traefik.http.routers.web-pub.rule=Host(`pub.${DOMAIN}`)
      - traefik.http.routers.web-pub.entrypoints=wan
      - traefik.http.routers.web-local.rule=Host(`web.local`)
      - traefik.http.services.web.loadbalancer.server.port=80
"""


def _ctx(tmp_path: Path, compose: str = COMPOSE) -> HookContext:
    cfg = make_config(tmp_path, {"web": "h1"})
    (cfg.get_stack_dir("web") / "compose.yaml").write_text(compose)
    (cfg.get_stack_dir("web") / ".env").write_text("DOMAIN=lab.test\n")
    return HookContext(cfg, "web", "h1")


class TestOptions:
    """Option validation."""

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({}, "entrypoints_require is required"),
            ({"entrypoints_require": {}, "nope": 1}, "unknown option"),
            ({"entrypoints_require": ["wan"]}, "entrypoints_require must map"),
            ({"entrypoints_require": {"wan": [1]}}, "entrypoints_require.wan"),
        ],
    )
    def test_invalid(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            policy.TraefikPolicyPlugin(options)


class TestBeforeUp:
    """Direct starts (up --host/--service) skip preflight, so before_up enforces it too."""

    async def test_refuses_to_start(self, tmp_path: Path) -> None:
        plugin = policy.TraefikPolicyPlugin({"entrypoints_require": {"wan": ["websecure"]}})
        with pytest.raises(PluginError, match="router web-pub is on entrypoint wan"):
            await plugin.before_up(_ctx(tmp_path))

    async def test_compliant_stack_starts(self, tmp_path: Path) -> None:
        plugin = policy.TraefikPolicyPlugin({"entrypoints_require": {"web": ["websecure"]}})
        await plugin.before_up(_ctx(tmp_path))


class TestPreflight:
    """Routers on an entrypoint must also be on the required ones."""

    async def test_reports_routers_missing_a_required_entrypoint(self, tmp_path: Path) -> None:
        plugin = policy.TraefikPolicyPlugin({"entrypoints_require": {"wan": ["websecure"]}})
        assert await plugin.preflight(_ctx(tmp_path)) == [
            "router web-pub is on entrypoint wan but not on websecure"
        ]

    async def test_accepts_a_single_string(self, tmp_path: Path) -> None:
        plugin = policy.TraefikPolicyPlugin({"entrypoints_require": {"wan": "websecure"}})
        assert len(await plugin.preflight(_ctx(tmp_path))) == 1

    async def test_compliant_stack(self, tmp_path: Path) -> None:
        plugin = policy.TraefikPolicyPlugin({"entrypoints_require": {"web": ["websecure"]}})
        assert await plugin.preflight(_ctx(tmp_path)) == []

    async def test_stack_without_labels(self, tmp_path: Path) -> None:
        plugin = policy.TraefikPolicyPlugin({"entrypoints_require": {"wan": ["websecure"]}})
        assert await plugin.preflight(_ctx(tmp_path, "services: {}\n")) == []


def test_package_registers_entry_point() -> None:
    pyproject = (
        Path(__file__).parent.parent / "examples" / "plugins" / "traefik-policy" / "pyproject.toml"
    )
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["entry-points"]["compose_farm.plugins"] == {
        "traefik-policy": "compose_farm_traefik_policy:TraefikPolicyPlugin"
    }
