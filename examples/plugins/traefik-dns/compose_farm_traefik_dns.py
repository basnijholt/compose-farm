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
from typing import Any

import yaml

from compose_farm.console import print_success
from compose_farm.executor import run_on_stacks
from compose_farm.plugins import ChangesContext, HookContext, Plugin, PluginError, run_hook
from compose_farm.traefik import generate_traefik_config

_HOST_ARGS = re.compile(r"Host\(([^)]*)\)")  # Host(`a`) or Host(`a`, `b`)
_QUOTED = re.compile(r"`([^`]*)`")
_HOSTNAME = re.compile(r"[A-Za-z0-9.-]+")
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
        path = ctx.cfg.compose_dir / self.file  # An absolute path replaces compose_dir
        old = path.read_text()
        new = self._replace_block(old, self._records(sorted(self._names(ctx))))
        if new == old:
            return
        path.write_text(new)
        print_success(f"traefik-dns: updated {path}")
        if not self.restart:
            return
        # Prepare the reader like `cf up` would, so e.g. the sync plugin copies the new
        # file to its hosts (hooks are idempotent by contract)
        for host in ctx.cfg.get_hosts(self.restart):
            await run_hook(HookContext(ctx.cfg, self.restart, host), "before_up")
        failed = [
            r.host for r in await run_on_stacks(ctx.cfg, [self.restart], "restart") if not r.success
        ]
        if failed:
            path.write_text(old)  # So the next run sees the change again and retries
            msg = f"restart of {self.restart} failed on {', '.join(failed)}"
            raise PluginError(msg)

    def _names(self, ctx: ChangesContext) -> set[str]:
        rules: list[str] = []
        for stack in ctx.cfg.stacks:
            try:
                dynamic, _ = generate_traefik_config(ctx.cfg, [stack], check_all=True)
            except (FileNotFoundError, ValueError) as e:
                # Skipping the stack would delete its records; keep the old ones instead
                msg = f"cannot read Traefik labels of {stack}: {e}"
                raise PluginError(msg) from e
            routers = dynamic.get("http", {}).get("routers", {})
            rules.extend(str(router.get("rule", "")) for router in routers.values())
        for rule_file in self.rule_files:
            rules.extend(_rules(yaml.safe_load((ctx.cfg.compose_dir / rule_file).read_text())))
        names = {
            name
            for rule in rules
            for args in _HOST_ARGS.findall(rule)
            for name in _QUOTED.findall(args)
            if _HOSTNAME.fullmatch(name)
        }
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
        if stop < start:
            msg = f"'{self.begin}' must come before '{self.end}' in {self.file}"
            raise PluginError(msg)
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        block = [f"{indent}{record}\n" for record in records]
        return "".join([*lines[: start + 1], *block, *lines[stop:]])


def _rules(node: object) -> list[str]:
    """Every ``rule:`` value in a Traefik dynamic config file (comments are not values)."""
    if isinstance(node, dict):
        return [
            rule
            for key, value in node.items()
            for rule in ([value] if key == "rule" and isinstance(value, str) else _rules(value))
        ]
    if isinstance(node, list):
        return [rule for item in node for rule in _rules(item)]
    return []


def _required(options: dict[str, Any], key: str) -> str:
    value = options.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{key} is required"
        raise PluginError(msg)
    return value
