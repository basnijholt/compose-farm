"""Lifecycle hook and CLI plugin framework for compose-farm."""

from .manager import (
    HookExecutionError,
    HookManager,
    PluginCLIRegistrar,
    list_available_plugins,
    register_cli_commands,
)
from .types import HookContext, HookEvent, HookPolicy, HookRegistration, HookResult

__all__ = [
    "HookContext",
    "HookEvent",
    "HookExecutionError",
    "HookManager",
    "HookPolicy",
    "HookRegistration",
    "HookResult",
    "PluginCLIRegistrar",
    "list_available_plugins",
    "register_cli_commands",
]
