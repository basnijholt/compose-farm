"""Plugin hook ordering in the up, migration, and rollback flows."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, patch

from compose_farm.executor import CommandResult
from compose_farm.operations import (
    PreflightResult,
    stop_orphaned_stacks,
    stop_stray_stacks,
    up_stacks,
    up_stacks_direct,
)
from compose_farm.state import get_stack_host, load_state, set_stack_host
from tests.plugin_helpers import Recorder, make_config, use_plugins

if TYPE_CHECKING:
    from pathlib import Path

    from compose_farm.config import Config


class Harness(ExitStack):
    """Fakes compose execution in operations and records it alongside hook events."""

    def __init__(
        self,
        events: list[Any],
        *,
        failing: set[tuple[str, str]] | None = None,
        was_running: bool = True,
    ) -> None:
        """Record into events; fail (command, host) pairs; report was_running for sources."""
        super().__init__()
        self.events = events
        self.failing = failing or set()  # (compose command, host) pairs that fail
        self.was_running = was_running

    def _result(self, stack: str, command: str, host: str) -> CommandResult:
        self.events.append(("compose", stack, command, host))
        ok = (command, host) not in self.failing
        return CommandResult(stack=stack, exit_code=0 if ok else 1, success=ok, host=host)

    async def _preflight(self, cfg: Config, stack: str, host: str) -> PreflightResult:
        self.events.append(("core_preflight", stack, host))
        return PreflightResult([], [], [], [])

    async def _run_compose(self, cfg: Config, stack: str, command: str, **_: Any) -> CommandResult:
        return self._result(stack, command, cfg.get_hosts(stack)[0])

    async def _run_compose_on_host(
        self, cfg: Config, stack: str, host: str, command: str, **_: Any
    ) -> CommandResult:
        return self._result(stack, command, host)

    async def _run_compose_step(
        self, cfg: Config, stack: str, command: str, *, raw: bool, host: str | None = None
    ) -> CommandResult:
        return self._result(stack, command, host or cfg.get_hosts(stack)[0])

    async def _run_command(
        self, host: Any, command: str, stack: str, **kwargs: Any
    ) -> CommandResult:
        return self._result(stack, command.split("docker compose ", 1)[1], kwargs["host_name"])

    def __enter__(self) -> Self:
        """Install the patches."""
        super().__enter__()
        ops = "compose_farm.operations"
        self.enter_context(patch(f"{ops}.check_stack_requirements", side_effect=self._preflight))
        self.enter_context(patch(f"{ops}.run_compose", side_effect=self._run_compose))
        self.enter_context(
            patch(f"{ops}.run_compose_on_host", side_effect=self._run_compose_on_host)
        )
        self.enter_context(patch(f"{ops}._run_compose_step", side_effect=self._run_compose_step))
        self.enter_context(patch(f"{ops}.run_command", side_effect=self._run_command))
        self.enter_context(
            patch(f"{ops}.check_stack_running", AsyncMock(return_value=self.was_running))
        )
        return self


def _recorder(events: list[Any], **options: Any) -> Recorder:
    plugin = Recorder({"events": events, **options})
    plugin.name = "rec"
    return plugin


class TestSimpleUp:
    """Hooks around a plain single-host up."""

    async def test_hook_order(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert result.success
        assert events == [
            ("before_up", "web", "h1", None),
            ("core_preflight", "web", "h1"),
            ("compose", "web", "up -d", "h1"),
            ("after_up", "web", "h1", None),
        ]

    async def test_before_up_failure_only_fails_that_stack(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("before_up", "bad")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1", "bad": "h1"}), plugin)
        with Harness(events):
            results = {r.stack: r for r in await up_stacks(cfg, ["web", "bad"])}
        assert results["web"].success
        assert not results["bad"].success
        assert "plugin rec.before_up: before_up failed" in results["bad"].stderr
        assert ("compose", "bad", "up -d", "h1") not in events
        assert get_stack_host(cfg, "bad") is None

    async def test_after_up_failure_is_a_warning(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("after_up", "web")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), plugin)
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert result.success
        assert get_stack_host(cfg, "web") == "h1"


class TestMigration:
    """Hooks, rollback, and guards during migration."""

    async def test_hook_order(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "h1")
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert result.success
        assert events == [
            ("before_up", "web", "h2", "h1"),
            ("core_preflight", "web", "h2"),
            ("compose", "web", "pull --ignore-buildable", "h2"),
            ("compose", "web", "build", "h2"),
            ("compose", "web", "down", "h1"),
            ("after_source_stopped", "web", "h2", "h1"),
            ("compose", "web", "up -d", "h2"),
            ("after_up", "web", "h2", "h1"),
        ]
        assert get_stack_host(cfg, "web") == "h2"

    async def test_after_source_stopped_failure_rolls_back(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("after_source_stopped", "web")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), plugin)
        set_stack_host(cfg, "web", "h1")
        with Harness(events):
            [result] = await up_stacks(cfg, ["web"])
        assert not result.success
        assert events[-1] == ("compose", "web", "up -d", "h1")
        assert ("compose", "web", "up -d", "h2") not in events
        assert get_stack_host(cfg, "web") == "h1"

    async def test_no_rollback_when_source_was_not_running(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("after_source_stopped", "web")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), plugin)
        set_stack_host(cfg, "web", "h1")
        with Harness(events, was_running=False):
            await up_stacks(cfg, ["web"])
        assert ("compose", "web", "up -d", "h1") not in events

    async def test_failed_cleanup_skips_source_restart(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "h1")
        with Harness(events, failing={("up -d", "h2"), ("down", "h2")}):
            [result] = await up_stacks(cfg, ["web"])
        assert not result.success
        assert ("compose", "web", "down", "h2") in events
        assert ("compose", "web", "up -d", "h1") not in events

    async def test_failed_target_up_rolls_back(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "h1")
        with Harness(events, failing={("up -d", "h2")}):
            await up_stacks(cfg, ["web"])
        assert events[-2:] == [
            ("compose", "web", "down", "h2"),
            ("compose", "web", "up -d", "h1"),
        ]
        assert not any(e[0] == "after_up" for e in events)

    async def test_source_host_passed_when_not_in_config(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h2"}), _recorder(events))
        set_stack_host(cfg, "web", "gone")
        with Harness(events):
            await up_stacks(cfg, ["web"])
        assert events[0] == ("before_up", "web", "h2", "gone")
        assert not any(e[0] == "after_source_stopped" for e in events)
        assert ("compose", "web", "up -d", "h2") in events


class TestMultiHost:
    """Multi-host barrier and per-host after_up."""

    async def test_barrier_and_after_up_only_for_successes(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"glances": ["h1", "h2"]}), _recorder(events))
        with Harness(events, failing={("up -d", "h2")}):
            await up_stacks(cfg, ["glances"])
        assert events == [
            ("before_up", "glances", "h1", None),
            ("core_preflight", "glances", "h1"),
            ("before_up", "glances", "h2", None),
            ("core_preflight", "glances", "h2"),
            ("compose", "glances", "up -d", "h1"),
            ("compose", "glances", "up -d", "h2"),
            ("after_up", "glances", "h1", None),
        ]

    async def test_before_up_failure_starts_no_host(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("before_up", "glances")])
        cfg = use_plugins(make_config(tmp_path, {"glances": ["h1", "h2"]}), plugin)
        with Harness(events):
            [result] = await up_stacks(cfg, ["glances"])
        assert not result.success
        assert result.label == "glances@h1"
        assert not any(e[0] == "compose" for e in events)


def _fake_run_on_stacks(failing: set[str] | None = None) -> AsyncMock:
    async def run(cfg: Config, stacks: list[str], cmd: str, **kwargs: Any) -> list[CommandResult]:
        results = []
        for stack in stacks:
            hosts = [kwargs["filter_host"]] if kwargs.get("filter_host") else cfg.get_hosts(stack)
            for host in hosts:
                ok = stack not in (failing or set())
                results.append(
                    CommandResult(stack=stack, exit_code=int(not ok), success=ok, host=host)
                )
        return results

    return AsyncMock(side_effect=run)


class TestDirectUp:
    """Hooks around up --service and up --host."""

    async def test_service_up_runs_hooks(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        fake = _fake_run_on_stacks()
        with patch("compose_farm.operations.run_on_stacks", fake):
            [result] = await up_stacks_direct(cfg, ["web"], "up -d app", raw=True)
        assert result.success
        fake.assert_awaited_once_with(cfg, ["web"], "up -d app", raw=True, filter_host=None)
        assert events == [("before_up", "web", "h1", None), ("after_up", "web", "h1", None)]

    async def test_before_up_failure_skips_stack(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("before_up", "bad")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1", "bad": "h1"}), plugin)
        fake = _fake_run_on_stacks()
        with patch("compose_farm.operations.run_on_stacks", fake):
            results = await up_stacks_direct(cfg, ["web", "bad"], "up -d", filter_host="h1")
        assert fake.await_args is not None
        assert fake.await_args.args[1] == ["web"]
        assert {r.stack: r.success for r in results} == {"web": True, "bad": False}

    async def test_host_filter_limits_multi_host_hooks(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"glances": ["h1", "h2"]}), _recorder(events))
        with patch("compose_farm.operations.run_on_stacks", _fake_run_on_stacks()):
            await up_stacks_direct(cfg, ["glances"], "up -d", filter_host="h2")
        assert events == [
            ("before_up", "glances", "h2", None),
            ("after_up", "glances", "h2", None),
        ]

    async def test_no_after_up_for_failed_run(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        with patch("compose_farm.operations.run_on_stacks", _fake_run_on_stacks({"web"})):
            await up_stacks_direct(cfg, ["web"], "up -d")
        assert events == [("before_up", "web", "h1", None)]


def _fake_down() -> AsyncMock:
    async def down(cfg: Config, stack: str, host: str, command: str, **_: Any) -> CommandResult:
        return CommandResult(
            stack=stack, exit_code=0, success=True, host=host, label=f"{stack}@{host}"
        )

    return AsyncMock(side_effect=down)


class TestStackRemoved:
    """on_stack_removed for orphans only; failures keep state for retry."""

    async def test_orphan_fires_hook_and_leaves_state(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        set_stack_host(cfg, "old", "h1")
        with patch("compose_farm.operations.run_compose_on_host", _fake_down()):
            [result] = await stop_orphaned_stacks(cfg)
        assert result.success
        assert events == [("on_stack_removed", "old", "h1", None)]
        assert "old" not in load_state(cfg)

    async def test_hook_failure_keeps_state_for_retry(self, tmp_path: Path) -> None:
        events: list[Any] = []
        plugin = _recorder(events, fail=[("on_stack_removed", "old")])
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), plugin)
        set_stack_host(cfg, "old", "h1")
        with patch("compose_farm.operations.run_compose_on_host", _fake_down()):
            [result] = await stop_orphaned_stacks(cfg)
        assert not result.success
        assert "will retry" in result.stderr
        assert load_state(cfg)["old"] == "h1"

    async def test_strays_do_not_fire_hook(self, tmp_path: Path) -> None:
        events: list[Any] = []
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), _recorder(events))
        with patch("compose_farm.operations.run_compose_on_host", _fake_down()):
            await stop_stray_stacks(cfg, {"web": ["h2"]})
        assert events == []
