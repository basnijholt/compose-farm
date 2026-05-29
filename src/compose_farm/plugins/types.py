"""Types for compose-farm lifecycle hook plugins."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from compose_farm.config import Config


class HookEvent(StrEnum):
    """Supported lifecycle hook events."""

    PRE_APPLY = "pre_apply"
    POST_APPLY = "post_apply"
    PRE_UP = "pre_up"
    POST_UP = "post_up"
    UP_FAILED = "up_failed"
    PRE_DOWN = "pre_down"
    POST_DOWN = "post_down"
    DOWN_FAILED = "down_failed"
    PRE_MIGRATE = "pre_migrate"
    PRE_STOP_SOURCE = "pre_stop_source"
    POST_STOP_SOURCE = "post_stop_source"
    PRE_START_TARGET = "pre_start_target"
    POST_START_TARGET = "post_start_target"
    MIGRATE_FAILED = "migrate_failed"
    ROLLBACK_STARTED = "rollback_started"
    ROLLBACK_COMPLETED = "rollback_completed"


class HookPolicy(StrEnum):
    """Failure policy for a lifecycle hook."""

    BLOCKING = "blocking"
    WARN = "warn"


@dataclass(frozen=True)
class HookContext:
    """Context passed to a lifecycle hook."""

    event: HookEvent
    stack: str
    config: Config
    source_host: str | None = None
    target_host: str | None = None
    operation_id: str | None = None
    dry_run: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookResult:
    """Result of an executed lifecycle hook."""

    plugin: str
    hook: str
    event: HookEvent
    success: bool
    message: str = ""


HookHandler = Callable[[HookContext], HookResult | Awaitable[HookResult] | None]


@dataclass(frozen=True)
class HookRegistration:
    """A plugin's registration for a lifecycle event."""

    plugin: str
    hook: str
    event: HookEvent
    handler: HookHandler
    policy: HookPolicy = HookPolicy.BLOCKING
