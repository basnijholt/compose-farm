"""Tests for operations module."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm.cli import lifecycle
from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult, RemoteCheckError
from compose_farm.operations import (
    PreflightResult,
    _migrate_stack,
    _report_preflight_failures,
    _stop_stacks_on_hosts,
    _up_action_label,
    _up_stack_simple,
    build_discovery_results,
    build_up_cmd,
    check_stack_requirements,
    up_stacks,
)
from compose_farm.plugins import HookExecutionError


@pytest.fixture
def basic_config(tmp_path: Path) -> Config:
    """Create a basic test config."""
    compose_dir = tmp_path / "compose"
    stack_dir = compose_dir / "test-service"
    stack_dir.mkdir(parents=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}")
    return Config(
        compose_dir=compose_dir,
        hosts={
            "host1": Host(address="localhost"),
            "host2": Host(address="localhost"),
        },
        stacks={"test-service": "host2"},
    )


class TestMigrationCommands:
    """Tests for migration command sequence."""

    @pytest.fixture
    def config(self, tmp_path: Path) -> Config:
        """Create a test config."""
        compose_dir = tmp_path / "compose"
        stack_dir = compose_dir / "test-service"
        stack_dir.mkdir(parents=True)
        (stack_dir / "docker-compose.yml").write_text("services: {}")
        return Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="localhost"),
                "host2": Host(address="localhost"),
            },
            stacks={"test-service": "host2"},
        )

    async def test_migration_uses_pull_ignore_buildable(self, config: Config) -> None:
        """Migration should use 'pull --ignore-buildable' to skip buildable images."""
        commands_called: list[str] = []

        async def mock_run_compose_step(
            cfg: Config,
            stack: str,
            command: str,
            *,
            raw: bool,
            host: str | None = None,
        ) -> CommandResult:
            commands_called.append(command)
            return CommandResult(
                stack=stack,
                exit_code=0,
                success=True,
            )

        with patch(
            "compose_farm.operations._run_compose_step",
            side_effect=mock_run_compose_step,
        ):
            await _migrate_stack(
                config,
                "test-service",
                current_host="host1",
                target_host="host2",
                prefix="[test]",
                raw=False,
            )

        # Migration should call pull with --ignore-buildable, then build, then down
        assert "pull --ignore-buildable" in commands_called
        assert "build" in commands_called
        assert "down" in commands_called
        # pull should come before build
        pull_idx = commands_called.index("pull --ignore-buildable")
        build_idx = commands_called.index("build")
        assert pull_idx < build_idx

    async def test_migration_dispatches_hooks_in_order(self, config: Config) -> None:
        """Migration dispatches lifecycle hooks in the expected order."""
        events_called: list[str] = []

        async def mock_dispatch_hook(
            cfg: Config,
            event: object,
            stack: str,
            *,
            source_host: str | None = None,
            target_host: str | None = None,
            metadata: dict[str, str] | None = None,
        ) -> None:
            _ = (cfg, stack, source_host, target_host, metadata)
            events_called.append(str(event))

        async def mock_run_compose_step(
            cfg: Config,
            stack: str,
            command: str,
            *,
            raw: bool,
            host: str | None = None,
        ) -> CommandResult:
            _ = (cfg, stack, command, raw, host)
            return CommandResult(stack="test-service", exit_code=0, success=True)

        with (
            patch("compose_farm.operations._dispatch_hook", side_effect=mock_dispatch_hook),
            patch("compose_farm.operations._run_compose_step", side_effect=mock_run_compose_step),
        ):
            result = await _migrate_stack(
                config,
                "test-service",
                current_host="host1",
                target_host="host2",
                prefix="[test]",
                raw=False,
            )

        assert result is None
        assert events_called == [
            "pre_migrate",
            "pre_stop_source",
            "post_stop_source",
            "pre_start_target",
        ]

    async def test_migration_dispatches_failure_hook_on_failed_down(self, config: Config) -> None:
        """Failed down step dispatches migrate_failed hook."""
        events_called: list[str] = []

        async def mock_dispatch_hook(
            cfg: Config,
            event: object,
            stack: str,
            *,
            source_host: str | None = None,
            target_host: str | None = None,
            metadata: dict[str, str] | None = None,
        ) -> None:
            _ = (cfg, stack, source_host, target_host, metadata)
            events_called.append(str(event))

        async def mock_run_compose_step(
            cfg: Config,
            stack: str,
            command: str,
            *,
            raw: bool,
            host: str | None = None,
        ) -> CommandResult:
            _ = (cfg, stack, raw, host)
            return CommandResult(
                stack="test-service",
                exit_code=1 if command == "down" else 0,
                success=command != "down",
            )

        with (
            patch("compose_farm.operations._dispatch_hook", side_effect=mock_dispatch_hook),
            patch("compose_farm.operations._run_compose_step", side_effect=mock_run_compose_step),
        ):
            result = await _migrate_stack(
                config,
                "test-service",
                current_host="host1",
                target_host="host2",
                prefix="[test]",
                raw=False,
            )

        assert result is not None
        assert events_called[-1] == "migrate_failed"

    @pytest.mark.parametrize("was_running", [True, False])
    async def test_migration_hook_failure_restores_original_running_state(
        self,
        config: Config,
        *,
        was_running: bool,
    ) -> None:
        """A hook failure after source shutdown restores only a previously running source."""
        events_called: list[str] = []

        async def mock_dispatch_hook(
            cfg: Config,
            event: object,
            stack: str,
            *,
            source_host: str | None = None,
            target_host: str | None = None,
            metadata: dict[str, str] | None = None,
        ) -> None:
            _ = (cfg, stack, source_host, target_host, metadata)
            events_called.append(str(event))
            if str(event) == "post_stop_source":
                message = "relay failed"
                raise HookExecutionError(message)

        success = CommandResult(stack="test-service", exit_code=0, success=True)
        with (
            patch("compose_farm.operations._dispatch_hook", side_effect=mock_dispatch_hook),
            patch("compose_farm.operations._run_compose_step", return_value=success),
            patch("compose_farm.operations.run_compose", return_value=success),
            patch("compose_farm.operations.run_compose_on_host", return_value=success) as mock_up,
        ):
            result = await _migrate_stack(
                config,
                "test-service",
                current_host="host1",
                target_host="host2",
                prefix="[test]",
                raw=False,
                was_running=was_running,
            )

        assert result is not None
        assert not result.success
        assert "migrate_failed" in events_called
        if was_running:
            mock_up.assert_awaited_once_with(
                config,
                "test-service",
                "host1",
                "up -d",
                raw=False,
            )
        else:
            mock_up.assert_not_awaited()


class TestBuildUpCmd:
    """Tests for build_up_cmd helper."""

    def test_basic(self) -> None:
        """Basic up command without flags."""
        assert build_up_cmd() == "up -d"

    def test_with_pull(self) -> None:
        """Up command with pull flag."""
        assert build_up_cmd(pull=True) == "up -d --pull always"

    def test_with_build(self) -> None:
        """Up command with build flag."""
        assert build_up_cmd(build=True) == "up -d --build"

    def test_with_pull_and_build(self) -> None:
        """Up command with both flags."""
        assert build_up_cmd(pull=True, build=True) == "up -d --pull always --build"

    def test_with_service(self) -> None:
        """Up command targeting a specific service."""
        assert build_up_cmd(service="web") == "up -d web"

    def test_with_all_options(self) -> None:
        """Up command with all options."""
        assert (
            build_up_cmd(pull=True, build=True, service="web") == "up -d --pull always --build web"
        )


class TestUpActionLabel:
    """Tests for user-facing up action labels."""

    def test_plain_up_starts(self) -> None:
        """A plain up operation starts missing or stopped containers."""
        assert _up_action_label() == "Starting"

    @pytest.mark.parametrize(("pull", "build"), [(True, False), (False, True), (True, True)])
    def test_pull_or_build_updates(self, pull: bool, build: bool) -> None:
        """Update-style operations should not imply containers were restarted."""
        assert _up_action_label(pull=pull, build=build) == "Updating"


class TestPreflightRequirements:
    """Tests for stack preflight checks."""

    async def test_remote_check_error_is_not_reported_as_missing_paths(
        self, basic_config: Config
    ) -> None:
        """A failed SSH check should be distinct from real missing paths."""
        with patch(
            "compose_farm.operations.check_paths_exist",
            side_effect=RemoteCheckError(
                "host2",
                "mount-check",
                "Permission denied for user basnijholt",
            ),
        ):
            result = await check_stack_requirements(basic_config, "test-service", "host2")

        assert result.ok is False
        assert result.missing_paths == []
        assert result.missing_networks == []
        assert result.missing_devices == []
        assert result.check_errors == [
            "mount-check failed on host2: Permission denied for user basnijholt"
        ]

    def test_report_preflight_failures_includes_remote_check_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """User-facing preflight output should show the real remote-check cause."""
        _report_preflight_failures(
            "test-service",
            "host2",
            PreflightResult(
                missing_paths=[],
                missing_networks=[],
                missing_devices=[],
                check_errors=["mount-check failed on host2: Permission denied"],
            ),
        )

        captured = capsys.readouterr()
        assert "remote check failed" in captured.err
        assert "Permission denied" in captured.err
        assert "missing path" not in captured.err

    async def test_up_stacks_preflight_failure_includes_host(
        self,
        basic_config: Config,
    ) -> None:
        """Synthetic preflight failures should still report the target host."""
        preflight = PreflightResult(
            missing_paths=["/mnt/data/test-service"],
            missing_networks=[],
            missing_devices=[],
            check_errors=[],
        )

        with patch("compose_farm.operations.check_stack_requirements", return_value=preflight):
            results = await up_stacks(basic_config, ["test-service"])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].stack == "test-service"
        assert results[0].host == "host2"

    async def test_up_stacks_multi_host_forces_streamed_output(self, tmp_path: Path) -> None:
        """Even raw=True must not allocate multiple compose TTY renderers."""
        compose_dir = tmp_path / "compose"
        (compose_dir / "glances").mkdir(parents=True)
        (compose_dir / "glances" / "docker-compose.yml").write_text("services: {}\n")
        cfg = Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="192.168.1.1"),
                "host2": Host(address="192.168.1.2"),
            },
            stacks={"glances": ["host1", "host2"]},
        )
        preflight = PreflightResult([], [], [], [])

        async def run_result(*args: object, **kwargs: object) -> CommandResult:
            return CommandResult(
                stack="glances",
                exit_code=0,
                success=True,
                host=str(kwargs.get("host_name", "")),
                label=str(kwargs.get("label", "")),
            )

        with (
            patch("compose_farm.operations.check_stack_requirements", return_value=preflight),
            patch("compose_farm.operations.run_command", new_callable=AsyncMock) as mock_run,
            patch("compose_farm.operations.set_multi_host_stack"),
        ):
            mock_run.side_effect = run_result
            results = await up_stacks(cfg, ["glances"], raw=True, pull=True, build=True)

        assert len(results) == 2
        assert all(result.success for result in results)
        assert all(call.kwargs["raw"] is False for call in mock_run.call_args_list)
        assert all(call.kwargs["stream"] is True for call in mock_run.call_args_list)


class TestLifecycleHooks:
    """Tests for non-migration hook dispatch in operations."""

    async def test_up_stack_simple_dispatches_hooks(self, basic_config: Config) -> None:
        """Simple up emits pre_up and post_up on success."""
        events_called: list[str] = []

        async def mock_dispatch_hook(
            cfg: Config,
            event: object,
            stack: str,
            *,
            source_host: str | None = None,
            target_host: str | None = None,
            metadata: dict[str, str] | None = None,
        ) -> None:
            _ = (cfg, stack, source_host, target_host, metadata)
            events_called.append(str(event))

        with (
            patch("compose_farm.operations._dispatch_hook", side_effect=mock_dispatch_hook),
            patch(
                "compose_farm.operations.check_stack_requirements",
                return_value=PreflightResult([], [], [], []),
            ),
            patch(
                "compose_farm.operations.run_compose",
                return_value=CommandResult(stack="test-service", exit_code=0, success=True),
            ),
        ):
            result = await _up_stack_simple(basic_config, "test-service", raw=False)

        assert result.success
        assert events_called == ["pre_up", "post_up"]

    async def test_stop_stacks_dispatches_down_hooks(self, basic_config: Config) -> None:
        """Stopping stacks emits pre_down and post_down hooks."""
        events_called: list[str] = []

        async def mock_dispatch_hook(
            cfg: Config,
            event: object,
            stack: str,
            *,
            source_host: str | None = None,
            target_host: str | None = None,
            metadata: dict[str, str] | None = None,
        ) -> None:
            _ = (cfg, stack, source_host, target_host, metadata)
            events_called.append(str(event))

        with (
            patch("compose_farm.operations._dispatch_hook", side_effect=mock_dispatch_hook),
            patch(
                "compose_farm.operations.run_compose_on_host",
                return_value=CommandResult(stack="test-service@host2", exit_code=0, success=True),
            ),
        ):
            results = await _stop_stacks_on_hosts(
                basic_config,
                {"test-service": ["host2"]},
            )

        assert len(results) == 1
        assert results[0].success
        assert events_called == ["pre_down", "post_down"]


class TestUpdateCommandSequence:
    """Tests for update command sequence."""

    def test_update_delegates_to_up_with_pull_and_build(self) -> None:
        """Update command should delegate to up with pull=True and build=True."""
        source = inspect.getsource(lifecycle.update)

        # Verify update calls up with pull=True and build=True
        assert "up(" in source
        assert "pull=True" in source
        assert "build=True" in source


class TestBuildDiscoveryResults:
    """Tests for build_discovery_results function."""

    @pytest.fixture
    def config(self, tmp_path: Path) -> Config:
        """Create a test config with multiple stacks."""
        compose_dir = tmp_path / "compose"
        for stack in ["plex", "jellyfin", "sonarr"]:
            (compose_dir / stack).mkdir(parents=True)
            (compose_dir / stack / "docker-compose.yml").write_text("services: {}")

        return Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="localhost"),
                "host2": Host(address="localhost"),
            },
            stacks={"plex": "host1", "jellyfin": "host1", "sonarr": "host2"},
        )

    def test_discovers_correctly_running_stacks(self, config: Config) -> None:
        """Stacks running on correct hosts are discovered."""
        running_on_host = {
            "host1": {"plex", "jellyfin"},
            "host2": {"sonarr"},
        }

        discovered, strays, duplicates = build_discovery_results(config, running_on_host)

        assert discovered == {"plex": "host1", "jellyfin": "host1", "sonarr": "host2"}
        assert strays == {}
        assert duplicates == {}

    def test_detects_stray_stacks(self, config: Config) -> None:
        """Stacks running on wrong hosts are marked as strays."""
        running_on_host = {
            "host1": set(),
            "host2": {"plex"},  # plex should be on host1
        }

        discovered, strays, _duplicates = build_discovery_results(config, running_on_host)

        assert "plex" not in discovered
        assert strays == {"plex": ["host2"]}

    def test_detects_duplicates(self, config: Config) -> None:
        """Single-host stacks running on multiple hosts are duplicates."""
        running_on_host = {
            "host1": {"plex"},
            "host2": {"plex"},  # plex running on both hosts
        }

        discovered, strays, duplicates = build_discovery_results(
            config, running_on_host, stacks=["plex"]
        )

        # plex is correctly running on host1
        assert discovered == {"plex": "host1"}
        # plex is also a stray on host2
        assert strays == {"plex": ["host2"]}
        # plex is a duplicate (single-host stack on multiple hosts)
        assert duplicates == {"plex": ["host1", "host2"]}

    def test_filters_to_requested_stacks(self, config: Config) -> None:
        """Only returns results for requested stacks."""
        running_on_host = {
            "host1": {"plex", "jellyfin"},
            "host2": {"sonarr"},
        }

        discovered, _strays, _duplicates = build_discovery_results(
            config, running_on_host, stacks=["plex"]
        )

        # Only plex should be in results
        assert discovered == {"plex": "host1"}
        assert "jellyfin" not in discovered
        assert "sonarr" not in discovered
