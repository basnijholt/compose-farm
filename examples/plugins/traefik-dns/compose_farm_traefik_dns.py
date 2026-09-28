"""Example compose-farm plugin: keep DNS records in sync with Traefik hostnames.

After every cf command that changes deployments, collects the ``Host(`...`)``
rules under a domain from all stacks' Traefik labels (plus optional route
files), and writes one record per name, all pointing at your Traefik entry,
between ``# BEGIN <marker>`` and ``# END <marker>`` in a file. When the records
changed, it can restart the stack that reads the file::

    plugins:
      traefik-dns:
        file: headscale/config.yaml        # relative to compose_dir, or absolute
        domain: lab.example.com            # names equal to or under this domain
        address: 100.64.0.28               # where every name points (Traefik)
        format: headscale                  # headscale (extra_records) or hosts
        marker: MANAGED LAB DNS            # default: compose-farm traefik-dns
        rule_files: [traefik/dynamic.d/manual.yml]
        restart: headscale                 # optional stack to restart on change
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from compose_farm.console import print_success
from compose_farm.executor import run_compose
from compose_farm.plugins import ChangesContext, Plugin, PluginError
from compose_farm.traefik import generate_traefik_config

if TYPE_CHECKING:
    from pathlib import Path

_HOST_RULE = re.compile(r"Host\(`([^`]+)`\)")
_FORMATS = ("headscale", "hosts")


class TraefikDnsPlugin(Plugin):
    """Write DNS records for Traefik hostnames into a managed block of a file."""

    def __init__(self, options: dict[str, Any]) -> None:
        """Validate the file, domain, address, format, marker, rule_files, and restart."""
        super().__init__(options)
        allowed = {"file", "domain", "address", "format", "marker", "rule_files", "restart"}
        unknown = sorted(set(options) - allowed)
        if unknown:
            msg = f"unknown option(s): {', '.join(unknown)}"
            raise PluginError(msg)
        self.file = _required(options, "file")
        self.domain = _required(options, "domain")
        self.address = _required(options, "address")
        self.format = options.get("format", "headscale")
        if self.format not in _FORMATS:
            msg = f"format must be one of: {', '.join(_FORMATS)}"
            raise PluginError(msg)
        marker = options.get("marker", "compose-farm traefik-dns")
        self.begin, self.end = f"# BEGIN {marker}", f"# END {marker}"
        rule_files = options.get("rule_files", [])
        if not isinstance(rule_files, list) or not all(isinstance(f, str) for f in rule_files):
            msg = "rule_files must be a list of paths"
            raise PluginError(msg)
        self.rule_files: list[str] = rule_files
        self.restart: str | None = options.get("restart")

    async def after_changes(self, ctx: ChangesContext) -> None:
        """Rewrite the block if the names changed, then restart the configured stack."""
        if self.restart and self.restart not in ctx.cfg.stacks:
            msg = f"restart stack {self.restart!r} is not in the config"
            raise PluginError(msg)
        path = _resolve(ctx, self.file)
        old = path.read_text()
        new = self._replace_block(old, self._records(sorted(self._names(ctx))))
        if new == old:
            return
        path.write_text(new)
        print_success(f"traefik-dns: updated {path}")
        if self.restart:
            result = await run_compose(ctx.cfg, self.restart, "restart")
            if not result.success:
                msg = f"restart of {self.restart} failed (exit {result.exit_code})"
                raise PluginError(msg)

    def _names(self, ctx: ChangesContext) -> set[str]:
        rules: list[str] = []
        for stack in ctx.cfg.stacks:
            try:
                dynamic, _ = generate_traefik_config(ctx.cfg, [stack], check_all=True)
            except (FileNotFoundError, ValueError):
                continue  # compose-farm's own checks report broken stacks
            routers = dynamic.get("http", {}).get("routers", {})
            rules.extend(str(router.get("rule", "")) for router in routers.values())
        for rule_file in self.rule_files:
            lines = _resolve(ctx, rule_file).read_text().splitlines()
            rules.extend(line for line in lines if not line.lstrip().startswith("#"))
        names = {name for rule in rules for name in _HOST_RULE.findall(rule)}
        return {n for n in names if n == self.domain or n.endswith(f".{self.domain}")}

    def _records(self, names: list[str]) -> list[str]:
        if self.format == "hosts":
            return [f"{self.address} {name}" for name in names]
        kind = "AAAA" if ":" in self.address else "A"
        return [
            line
            for name in names
            for line in (f"- name: {name}", f"  type: {kind}", f"  value: {self.address}")
        ]

    def _replace_block(self, text: str, records: list[str]) -> str:
        lines = text.splitlines(keepends=True)
        stripped = [line.strip() for line in lines]
        if self.begin not in stripped or self.end not in stripped:
            msg = f"add '{self.begin}' and '{self.end}' lines to {self.file} where the records go"
            raise PluginError(msg)
        start, stop = stripped.index(self.begin), stripped.index(self.end)
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        block = [f"{indent}{record}\n" for record in records]
        return "".join([*lines[: start + 1], *block, *lines[stop:]])


def _required(options: dict[str, Any], key: str) -> str:
    value = options.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{key} is required"
        raise PluginError(msg)
    return value


def _resolve(ctx: ChangesContext, path: str) -> Path:
    """Paths are relative to compose_dir, which compose-farm reads locally."""
    return ctx.cfg.compose_dir / path  # An absolute path replaces compose_dir
