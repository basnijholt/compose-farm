"""Tests for plugin hook manager behavior."""

from __future__ import annotations

from pathlib import Path
import pytest

from compose_farm.config import Config, Host
from compose_farm.plugins import (
    HookContext,
    HookEvent,
    HookExecutionError,
    HookManager,
    HookPolicy,
    HookRegistration,
    HookResult,
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Build a minimal config for hook context tests."""
    return Config(
        compose_dir=tmp_path,
        hosts={"host1": Host(address="localhost")},
        stacks={"svc": "host1"},
    )


@pytest.mark.asyncio
async def test_blocking_hook_failure_raises(cfg: Config) -> None:
    """Blocking hook failure raises HookExecutionError."""

    def handler(_context: HookContext) -> HookResult:
        return HookResult(
            plugin="test",
            hook="bad",
            event=HookEvent.PRE_MIGRATE,
            success=False,
            message="boom",
        )

    manager = HookManager.from_registrations(
        [
            HookRegistration(
                plugin="test",
                hook="bad",
                event=HookEvent.PRE_MIGRATE,
                handler=handler,
                policy=HookPolicy.BLOCKING,
            )
        ]
    )

    with pytest.raises(HookExecutionError):
        await manager.dispatch(HookContext(event=HookEvent.PRE_MIGRATE, stack="svc", config=cfg))


@pytest.mark.asyncio
async def test_warn_hook_failure_continues(cfg: Config) -> None:
    """Warn policy returns failed result and does not raise."""

    def handler(_context: HookContext) -> HookResult:
        return HookResult(
            plugin="test",
            hook="warn",
            event=HookEvent.PRE_MIGRATE,
            success=False,
            message="non-fatal",
        )

    manager = HookManager.from_registrations(
        [
            HookRegistration(
                plugin="test",
                hook="warn",
                event=HookEvent.PRE_MIGRATE,
                handler=handler,
                policy=HookPolicy.WARN,
            )
        ]
    )

    results = await manager.dispatch(
        HookContext(event=HookEvent.PRE_MIGRATE, stack="svc", config=cfg)
    )
    assert len(results) == 1
    assert results[0].success is False


@pytest.mark.asyncio
async def test_async_hook_handler_supported(cfg: Config) -> None:
    """Async hook handlers are awaited."""

    async def handler(_context: HookContext) -> HookResult:
        return HookResult(
            plugin="test",
            hook="async",
            event=HookEvent.POST_START_TARGET,
            success=True,
        )

    manager = HookManager.from_registrations(
        [
            HookRegistration(
                plugin="test",
                hook="async",
                event=HookEvent.POST_START_TARGET,
                handler=handler,
            )
        ]
    )

    results = await manager.dispatch(
        HookContext(event=HookEvent.POST_START_TARGET, stack="svc", config=cfg)
    )
    assert len(results) == 1
    assert results[0].success is True
