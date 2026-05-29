"""Plugin loading and lifecycle hook dispatch."""

from __future__ import annotations

import inspect
from collections import defaultdict
from dataclasses import dataclass, replace
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from compose_farm.console import print_warning

from .types import HookContext, HookEvent, HookPolicy, HookRegistration, HookResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from importlib.metadata import EntryPoint

    import typer

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
        discovered = _apply_policy_overrides(discovered, cfg)
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

    @property
    def plugin_names(self) -> tuple[str, ...]:
        """Loaded plugin names in dispatch order."""
        return tuple(plugin.name for plugin in self._plugins)

    def hook_registrations(self) -> tuple[HookRegistration, ...]:
        """All registered hooks across plugins."""
        hooks: list[HookRegistration] = []
        for plugin in self._plugins:
            hooks.extend(plugin.hooks)
        return tuple(hooks)


def list_available_plugins() -> list[str]:
    """List plugin names discoverable from entry points."""
    eps = entry_points(group=ENTRYPOINT_GROUP)
    return sorted(ep.name for ep in eps)


class PluginCLIRegistrar:
    """Safe command registrar exposed to plugins for CLI extension.

    Prevents plugins from overriding existing top-level command/group names.
    """

    def __init__(self, app: typer.Typer, plugin_name: str, reserved_names: set[str]) -> None:
        """Create a plugin command registrar for one plugin."""
        self._app = app
        self._plugin_name = plugin_name
        self._reserved_names = reserved_names

    def command(self, name: str, **kwargs: Any) -> Callable[[Any], Any]:
        """Register a top-level command owned by a plugin."""
        self._assert_name_available(name)

        def decorator(func: Any) -> Any:
            command_decorator = self._app.command(name=name, **kwargs)
            wrapped = command_decorator(func)
            self._reserved_names.add(name)
            return wrapped

        return decorator

    def add_typer(self, sub_app: typer.Typer, *, name: str, **kwargs: Any) -> None:
        """Register a top-level subcommand group owned by a plugin."""
        self._assert_name_available(name)
        self._app.add_typer(sub_app, name=name, **kwargs)
        self._reserved_names.add(name)

    def _assert_name_available(self, name: str) -> None:
        if name in self._reserved_names:
            msg = (
                f"Plugin {self._plugin_name!r} cannot register CLI name {name!r}: "
                "name already exists"
            )
            raise HookExecutionError(msg)


def register_cli_commands(app: typer.Typer) -> tuple[str, ...]:
    """Allow plugins to register additional CLI commands/groups.

    Plugins can expose a callable `register_cli_commands(registrar)` method.
    Registration order follows plugin entry-point name order for deterministic behavior.
    """
    reserved_names = _collect_registered_cli_names(app)
    loaded_plugins: list[str] = []

    eps = sorted(entry_points(group=ENTRYPOINT_GROUP), key=lambda ep: ep.name)
    for ep in eps:
        loaded = ep.load()
        plugin_obj = loaded() if callable(loaded) else loaded
        register_commands = getattr(plugin_obj, "register_cli_commands", None)
        if register_commands is None:
            continue
        if not callable(register_commands):
            msg = f"Plugin {ep.name!r} has non-callable register_cli_commands"
            raise HookExecutionError(msg)

        registrar = PluginCLIRegistrar(app=app, plugin_name=ep.name, reserved_names=reserved_names)
        register_commands(registrar)
        loaded_plugins.append(ep.name)

    return tuple(loaded_plugins)


def _collect_registered_cli_names(app: typer.Typer) -> set[str]:
    """Collect existing top-level command/group names from a Typer app."""
    names: set[str] = set()

    for command_info in getattr(app, "registered_commands", []):
        explicit = getattr(command_info, "name", None)
        if isinstance(explicit, str) and explicit:
            names.add(explicit)
            continue

        callback = getattr(command_info, "callback", None)
        callback_name = getattr(callback, "__name__", None)
        if isinstance(callback_name, str) and callback_name:
            names.add(callback_name.replace("_", "-"))

    for group_info in getattr(app, "registered_groups", []):
        group_name = getattr(group_info, "name", None)
        if isinstance(group_name, str) and group_name:
            names.add(group_name)

    return names


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
