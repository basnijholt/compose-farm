"""Tests for built-in command-hooks plugin."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from compose_farm.config import Config, Host
from compose_farm.plugins import HookContext, HookEvent, HookResult
from compose_farm.plugins.builtin.command_hooks import CommandHooksPlugin


def _make_config(tmp_path: Path, hooks: dict[str, list[str]] | None = None) -> Config:
    """Create minimal config with command-hooks plugin enabled."""
    return Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="localhost")},
        stacks={"svc": "host1"},
        plugins=["command-hooks"],
        plugin_config={"command-hooks": {"hooks": hooks or {}}},
    )


def _handler_for_event(
    plugin: CommandHooksPlugin,
    event: HookEvent,
) -> Callable[[HookContext], HookResult | None]:
    """Return registered handler for a specific event."""
    for registration in plugin.register_hooks():
        if registration.event == event:
            return cast("Callable[[HookContext], HookResult | None]", registration.handler)
    msg = f"No handler for event {event.value}"
    raise AssertionError(msg)


def test_command_hooks_dry_run_renders_commands(tmp_path: Path) -> None:
    """Dry-run mode renders commands without executing."""
    cfg = _make_config(
        tmp_path, hooks={"pre_migrate": ["echo {stack}:{source_host}->{target_host}"]}
    )
    plugin = CommandHooksPlugin()
    handler = _handler_for_event(plugin, HookEvent.PRE_MIGRATE)

    context = HookContext(
        event=HookEvent.PRE_MIGRATE,
        stack="svc",
        config=cfg,
        source_host="host1",
        target_host="host2",
        dry_run=True,
    )
    result = handler(context)

    assert result is not None
    assert result.success
    assert "dry-run" in result.message
    assert "svc:host1->host2" in result.message


def test_command_hooks_executes_and_succeeds(tmp_path: Path) -> None:
    """Plugin executes configured command successfully."""
    cfg = _make_config(tmp_path, hooks={"pre_apply": ["true"]})
    plugin = CommandHooksPlugin()
    handler = _handler_for_event(plugin, HookEvent.PRE_APPLY)

    context = HookContext(
        event=HookEvent.PRE_APPLY,
        stack="*",
        config=cfg,
        dry_run=False,
    )
    result = handler(context)

    assert result is not None
    assert result.success


def test_command_hooks_fails_on_bad_placeholder(tmp_path: Path) -> None:
    """Unknown placeholders cause a failed hook result."""
    cfg = _make_config(tmp_path, hooks={"pre_apply": ["echo {unknown}"]})
    plugin = CommandHooksPlugin()
    handler = _handler_for_event(plugin, HookEvent.PRE_APPLY)

    context = HookContext(
        event=HookEvent.PRE_APPLY,
        stack="*",
        config=cfg,
        dry_run=False,
    )
    result = handler(context)

    assert result is not None
    assert not result.success
    assert "unknown placeholder" in result.message
