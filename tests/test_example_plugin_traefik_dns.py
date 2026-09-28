"""Tests for the example traefik-dns plugin in examples/plugins/traefik-dns."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm.executor import CommandResult
from compose_farm.plugins import ChangesContext, PluginError
from tests.plugin_helpers import load_example_plugin, make_config

if TYPE_CHECKING:
    from compose_farm.config import Config

dns = load_example_plugin("traefik_dns", folder="traefik-dns")


def _labels(*rules: str) -> str:
    lines = [f"      - traefik.http.routers.r{i}.rule={rule}" for i, rule in enumerate(rules)]
    return "services:\n  app:\n    image: nginx\n    labels:\n" + "\n".join(lines) + "\n"


HEADSCALE = """\
dns:
  extra_records:
    # BEGIN MANAGED DNS
    - name: stale.lab.test
      type: A
      value: 100.64.0.1
    # END MANAGED DNS
unix_socket: /var/run/headscale/headscale.sock
"""


def _cfg(tmp_path: Path) -> Config:
    cfg = make_config(tmp_path, {"web": "h1", "wiki": "h2", "headscale": "h1"})
    stacks = cfg.compose_dir
    (stacks / "web" / "compose.yaml").write_text(
        _labels("Host(`web.${DOMAIN}`)", "Host(`web.local`)", "Host(`web.other.org`)")
    )
    (stacks / "web" / ".env").write_text("DOMAIN=lab.test\n")
    (stacks / "wiki" / "compose.yaml").write_text(
        _labels("Host(`wiki.lab.test`) || Host(`docs.lab.test`)")
    )
    (stacks / "headscale" / "config.yaml").write_text(HEADSCALE)
    manual = stacks / "traefik" / "dynamic.d"
    manual.mkdir(parents=True)
    (manual / "manual.yml").write_text(
        "http:\n  routers:\n    ha:\n      rule: Host(`home.lab.test`)\n"
        "    # old:\n    #   rule: Host(`gone.lab.test`)\n"
    )
    return cfg


def _plugin(**options: Any) -> Any:
    return dns.TraefikDnsPlugin(
        {
            "file": "headscale/config.yaml",
            "domain": "lab.test",
            "address": "100.64.0.28",
            "marker": "MANAGED DNS",
            "rule_files": ["traefik/dynamic.d/manual.yml"],
            **options,
        }
    )


def _ok() -> AsyncMock:
    return AsyncMock(return_value=CommandResult(stack="headscale", exit_code=0, success=True))


class TestOptions:
    """Option validation."""

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({"domain": "x", "address": "1.2.3.4"}, "file is required"),
            ({"file": "f", "address": "1.2.3.4"}, "domain is required"),
            ({"file": "f", "domain": "x"}, "address is required"),
            ({"file": "f", "domain": "x", "address": "a", "format": "bind"}, "format must be"),
            ({"file": "f", "domain": "x", "address": "a", "rule_files": "m.yml"}, "rule_files"),
            ({"file": "f", "domain": "x", "address": "a", "nope": 1}, "unknown option"),
        ],
    )
    def test_invalid(self, options: dict[str, Any], error: str) -> None:
        with pytest.raises(PluginError, match=error):
            dns.TraefikDnsPlugin(options)


class TestHeadscale:
    """Records for every Host() under the domain, written between the markers."""

    async def test_writes_block_and_restarts_once(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        plugin = _plugin(restart="headscale")
        restart = _ok()
        with patch.object(dns, "run_compose", restart):
            await plugin.after_changes(ChangesContext(cfg, ("web",)))
            await plugin.after_changes(ChangesContext(cfg, ("web",)))  # Nothing changed
        text = (cfg.compose_dir / "headscale" / "config.yaml").read_text()
        names = [line.split()[-1] for line in text.splitlines() if "- name:" in line]
        assert names == ["docs.lab.test", "home.lab.test", "web.lab.test", "wiki.lab.test"]
        assert "    - name: docs.lab.test\n      type: A\n      value: 100.64.0.28\n" in text
        assert text.startswith("dns:\n  extra_records:\n    # BEGIN MANAGED DNS\n")
        assert text.endswith(
            "    # END MANAGED DNS\nunix_socket: /var/run/headscale/headscale.sock\n"
        )
        restart.assert_awaited_once_with(cfg, "headscale", "restart")

    async def test_ipv6_address_uses_aaaa(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        await _plugin(address="fd7a::1c").after_changes(ChangesContext(cfg, ("web",)))
        assert "type: AAAA" in (cfg.compose_dir / "headscale" / "config.yaml").read_text()

    async def test_failed_restart_is_an_error(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        failed = AsyncMock(
            return_value=CommandResult(stack="headscale", exit_code=1, success=False)
        )
        with patch.object(dns, "run_compose", failed), pytest.raises(PluginError, match="restart"):
            await _plugin(restart="headscale").after_changes(ChangesContext(cfg, ("web",)))

    async def test_unknown_restart_stack_is_an_error(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        with pytest.raises(PluginError, match="restart stack 'nope' is not in the config"):
            await _plugin(restart="nope").after_changes(ChangesContext(cfg, ("web",)))

    async def test_missing_markers_are_an_error(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        (cfg.compose_dir / "headscale" / "config.yaml").write_text("dns: {}\n")
        with pytest.raises(PluginError, match="add '# BEGIN MANAGED DNS' and '# END MANAGED DNS'"):
            await _plugin().after_changes(ChangesContext(cfg, ("web",)))


async def test_hosts_format(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    hosts_file = tmp_path / "custom.list"
    hosts_file.write_text("# BEGIN compose-farm traefik-dns\n# END compose-farm traefik-dns\n")
    plugin = dns.TraefikDnsPlugin(
        {"file": str(hosts_file), "domain": "lab.test", "address": "192.168.1.6", "format": "hosts"}
    )
    await plugin.after_changes(ChangesContext(cfg, ("web",)))
    assert hosts_file.read_text().splitlines()[1:-1] == [
        "192.168.1.6 docs.lab.test",
        "192.168.1.6 web.lab.test",
        "192.168.1.6 wiki.lab.test",
    ]


def test_package_registers_entry_point() -> None:
    pyproject = (
        Path(__file__).parent.parent / "examples" / "plugins" / "traefik-dns" / "pyproject.toml"
    )
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["entry-points"]["compose_farm.plugins"] == {
        "traefik-dns": "compose_farm_traefik_dns:TraefikDnsPlugin"
    }
