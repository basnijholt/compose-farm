"""Plugin loading and lifecycle hook dispatch."""

from __future__ import annotations

import inspect
from collections import defaultdict
<<<<<<< HEAD
from collections.abc import Iterable
from dataclasses import dataclass
=======
from dataclasses import dataclass, replace
>>>>>>> 9362a54 (fixup! feat(plugins): add lifecycle hook framework and config plumbing)
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from compose_farm.console import print_warning

from .types import HookContext, HookEvent, HookPolicy, HookRegistration, HookResult

if TYPE_CHECKING:
    from collections.abc import Iterable
    from importlib.metadata import EntryPoint

    from compose_farm.config import Config


ENTRYPOINT_GROUP = "compose_farm.plugins"


class HookExecutionError(RuntimeError):
    """Raised when a blocking hook fails."""


@dataclass(frozen=True)
class HookPlugin:
    """Loaded plugin plus its hook registrations."""

    name: str
    hooks: tuple[HookRegistration, ...]


class HookManager:
    """Loads plugins and dispatches lifecycle hooks."""

    def __init__(self, plugins: Iterable[HookPlugin]) -> None:
        """Initialize manager with loaded plugins."""
        self._plugins = tuple(plugins)
        by_event: dict[HookEvent, list[HookRegistration]] = defaultdict(list)
        for plugin in self._plugins:
            for hook in plugin.hooks:
                by_event[hook.event].append(hook)
        self._by_event = {event: tuple(hooks) for event, hooks in by_event.items()}

    @classmethod
    def empty(cls) -> HookManager:
        """Build a manager with no registered plugins."""
        return cls(plugins=[])

    @classmethod
    def from_registrations(cls, hooks: Iterable[HookRegistration]) -> HookManager:
        """Build a manager from direct registrations (used in tests)."""
        return cls(plugins=[HookPlugin(name="test", hooks=tuple(hooks))])

    @classmethod
    def from_config(cls, cfg: Config) -> HookManager:
        """Build a manager from config and installed entry-point plugins."""
        if not cfg.plugins:
            return cls.empty()

        discovered = _discover_plugins(enabled=cfg.plugins)
        return cls(plugins=discovered)

    async def dispatch(self, context: HookContext) -> list[HookResult]:
        """Dispatch a lifecycle event to registered hooks."""
        hooks = self._by_event.get(context.event, ())
        results: list[HookResult] = []

        for registration in hooks:
            try:
                maybe_result = registration.handler(context)
                raw_result = (
                    await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
                )
                result = _coerce_hook_result(raw_result, registration, context)

                if not result.success and registration.policy == HookPolicy.BLOCKING:
                    msg = (
                        f"Hook failed: {registration.plugin}:{registration.hook} "
                        f"({context.event.value})"
                    )
                    _raise_hook_error(msg)

                if not result.success:
                    print_warning(
                        "Hook warning: "
                        f"{registration.plugin}:{registration.hook} "
                        f"({context.event.value})"
                    )

                results.append(result)

            except HookExecutionError:
                raise
            except Exception as exc:
                if registration.policy == HookPolicy.BLOCKING:
                    msg = (
                        f"Hook crashed: {registration.plugin}:{registration.hook} "
                        f"({context.event.value}): {exc}"
                    )
                    raise HookExecutionError(msg) from exc

                print_warning(
                    "Hook warning: "
                    f"{registration.plugin}:{registration.hook} "
                    f"({context.event.value}): {exc}"
                )
                results.append(
                    HookResult(
                        plugin=registration.plugin,
                        hook=registration.hook,
                        event=context.event,
                        success=False,
                        message=str(exc),
                    )
                )

        return results


def _discover_plugins(*, enabled: list[str]) -> list[HookPlugin]:
    """Discover and load plugins from Python entry points."""
    eps = entry_points(group=ENTRYPOINT_GROUP)
    by_name: dict[str, EntryPoint] = {ep.name: ep for ep in eps}

    missing = [name for name in enabled if name not in by_name]
    if missing:
        missing_str = ", ".join(missing)
        msg = f"Configured plugin(s) not found via entry points: {missing_str}"
        raise HookExecutionError(msg)

    # Respect config.plugins order for deterministic hook dispatch.
    return [_load_plugin(by_name[name]) for name in enabled]


def _load_plugin(ep: EntryPoint) -> HookPlugin:
    """Load one plugin from an entry point."""
    loaded = ep.load()
    plugin_obj = loaded() if callable(loaded) else loaded

    register_hooks = getattr(plugin_obj, "register_hooks", None)
    if register_hooks is None or not callable(register_hooks):
        msg = f"Plugin {ep.name!r} must expose a callable register_hooks()"
        raise HookExecutionError(msg)

    hooks = tuple(register_hooks())
    return HookPlugin(name=ep.name, hooks=hooks)
<<<<<<< HEAD
=======


def _apply_policy_overrides(plugins: list[HookPlugin], cfg: Config) -> list[HookPlugin]:
    """Apply policy overrides from config.plugin_config to plugin hooks."""
    updated: list[HookPlugin] = []

    for plugin in plugins:
        policy_overrides = cfg.get_plugin_config(plugin.name).get("policies", {})
        if not isinstance(policy_overrides, dict):
            msg = f"Plugin {plugin.name!r} config.policies must be a mapping"
            raise HookExecutionError(msg)

        hooks: list[HookRegistration] = []
        for hook in plugin.hooks:
            override_raw = policy_overrides.get(hook.hook)
            if override_raw is None:
                hooks.append(hook)
                continue

            try:
                override_policy = HookPolicy(str(override_raw))
            except ValueError as exc:
                msg = f"Invalid policy override for {plugin.name}:{hook.hook}: {override_raw!r}"
                raise HookExecutionError(msg) from exc

            hooks.append(replace(hook, policy=override_policy))

        updated.append(HookPlugin(name=plugin.name, hooks=tuple(hooks)))

    return updated


def _coerce_hook_result(
    raw_result: object,
    registration: HookRegistration,
    context: HookContext,
) -> HookResult:
    """Normalize plugin hook return values to HookResult."""
    if raw_result is None:
        return HookResult(
            plugin=registration.plugin,
            hook=registration.hook,
            event=context.event,
            success=True,
        )
    if isinstance(raw_result, HookResult):
        return raw_result

    msg = (
        f"Hook {registration.plugin}:{registration.hook} "
        f"returned invalid type: {type(raw_result).__name__}"
    )
    raise HookExecutionError(msg)


def _raise_hook_error(message: str) -> None:
    """Raise a normalized hook execution error."""
    raise HookExecutionError(message)
>>>>>>> 9362a54 (fixup! feat(plugins): add lifecycle hook framework and config plumbing)
