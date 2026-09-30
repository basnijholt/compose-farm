"""Tests for executor module."""

import asyncio
import gc
import os
import shlex
import subprocess
import sys
import weakref
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any, Self
from unittest.mock import AsyncMock, patch

import pytest

from compose_farm.config import Config, Host
from compose_farm.executor import (
    CommandResult,
    RemoteCheckError,
    _build_compose_command,
    _run_local_command,
    _run_ssh_command,
    _stream_output_lines,
    build_ssh_command,
    check_networks_exist,
    check_paths_exist,
    check_stack_running,
    get_running_stacks_on_host,
    is_local,
    run_command,
    run_compose,
    run_compose_on_host,
    run_on_stacks,
)
from compose_farm.ssh_keys import SSH_KEY_PATH
from tests.plugin_helpers import Recorder, make_config, use_plugins

# These tests run actual shell commands that only work on Linux
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only shell commands")


async def _async_lines(lines: list[str | bytes]) -> AsyncIterator[str | bytes]:
    for line in lines:
        yield line


class _RecordingConsole:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


class TestStreamOutputLines:
    """Tests for streaming command output formatting."""

    def test_format_stack_prefix_uses_stable_distinct_colors(self) -> None:
        from compose_farm.console import format_stack_prefix

        assert format_stack_prefix("wakapi") == "[yellow]\\[wakapi][/]"
        assert format_stack_prefix("ollama") == "[bright_green]\\[ollama][/]"
        assert format_stack_prefix("openclaw") == "[bright_blue]\\[openclaw][/]"
        assert format_stack_prefix("wakapi") == format_stack_prefix("wakapi")

    async def test_stream_output_lines_uses_colored_prefix_and_escapes_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from compose_farm import executor
        from compose_farm.console import format_stack_prefix

        output = _RecordingConsole()
        monkeypatch.setattr(executor, "console", output)

        await _stream_output_lines(_async_lines(["Downloading [ok]\n"]), "wakapi")

        assert output.calls == [
            ((f"{format_stack_prefix('wakapi')} Downloading \\[ok]\n",), {"end": ""})
        ]


class TestIsLocal:
    """Tests for is_local function."""

    @pytest.mark.parametrize(
        "address",
        ["local", "localhost", "127.0.0.1", "::1", "LOCAL", "LOCALHOST"],
    )
    def test_local_addresses(self, address: str) -> None:
        host = Host(address=address)
        assert is_local(host) is True

    @pytest.mark.parametrize(
        "address",
        ["192.168.1.10", "nas01.local", "10.0.0.1", "example.com"],
    )
    def test_remote_addresses(self, address: str) -> None:
        host = Host(address=address)
        assert is_local(host) is False


class TestRunLocalCommand:
    """Tests for local command execution."""

    async def test_run_local_command_success(self) -> None:
        result = await _run_local_command("echo hello", "test-service")
        assert result.success is True
        assert result.exit_code == 0
        assert result.stack == "test-service"

    async def test_run_local_command_failure(self) -> None:
        result = await _run_local_command("exit 1", "test-service")
        assert result.success is False
        assert result.exit_code == 1

    async def test_run_local_command_not_found(self) -> None:
        result = await _run_local_command("nonexistent_command_xyz", "test-service")
        assert result.success is False
        assert result.exit_code != 0

    async def test_run_local_command_captures_output(self) -> None:
        result = await _run_local_command("echo hello", "test-service", stream=False)
        assert "hello" in result.stdout


class TestRunCommand:
    """Tests for run_command dispatcher."""

    async def test_run_command_local(self) -> None:
        host = Host(address="localhost")
        result = await run_command(host, "echo test", "test-service")
        assert result.success is True

    async def test_run_command_result_structure(self) -> None:
        host = Host(address="local")
        result = await run_command(host, "true", "my-service")
        assert isinstance(result, CommandResult)
        assert result.stack == "my-service"
        assert result.exit_code == 0
        assert result.success is True

    def test_compose_command_executes_hostile_path_literally(self, tmp_path: Path) -> None:
        """Exercise the generated command through a real shell with a fake Docker binary."""
        stack_dir = tmp_path / "$(touch injected)"
        stack_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD"\n')
        docker.chmod(0o755)

        command = _build_compose_command(stack_dir, "ps")
        result = subprocess.run(  # noqa: S602
            command,
            shell=True,
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={"PATH": f"{bin_dir}{os.pathsep}{os.defpath}"},
        )

        assert result.stdout.strip() == str(stack_dir)
        assert not (tmp_path / "injected").exists()

    async def test_non_streaming_ssh_error_is_returned_not_printed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Preflight checks run under progress bars and must not print mid-render."""
        from compose_farm import executor

        output = _RecordingConsole()
        monkeypatch.setattr(executor, "err_console", output)
        known_hosts = tmp_path / "known_hosts"
        known_hosts.touch()

        with (
            patch("compose_farm.executor.SSH_KNOWN_HOSTS_PATH", known_hosts),
            patch("asyncssh.connect", side_effect=OSError("Connection lost")),
        ):
            result = await _run_ssh_command(
                Host(address="192.168.1.10"),
                "true",
                "mount-check",
                stream=False,
            )

        assert result.success is False
        assert result.stderr == "Connection lost"
        assert output.calls == []

    async def test_missing_known_hosts_returns_setup_guidance(self, tmp_path: Path) -> None:
        """A missing trust database should produce an actionable failure."""
        missing = tmp_path / "known_hosts"

        with patch("compose_farm.executor.SSH_KNOWN_HOSTS_PATH", missing):
            result = await _run_ssh_command(
                Host(address="192.168.1.10"),
                "true",
                "mount-check",
                stream=False,
            )

        assert result.success is False
        assert "known_hosts" in result.stderr
        assert "cf ssh setup --trust-only" in result.stderr


class _FakeProcess:
    """Minimal asyncssh process: empty output, exit 0, optionally blocks until released."""

    def __init__(self, release: asyncio.Event | None, running: list[int], ssh: "_FakeSsh") -> None:
        self._release = release
        self._running = running
        self._ssh = ssh
        self.exit_status = 0
        self.stdout = AsyncMock(read=AsyncMock(return_value=""))
        self.stderr = AsyncMock(read=AsyncMock(return_value=""))

    async def __aenter__(self) -> Self:
        self._running[0] += 1
        if self._running[0] == self._ssh.expect_running:
            self._ssh.all_running.set()
        return self

    async def __aexit__(self, *args: object) -> None:
        self._running[0] -= 1

    async def wait(self) -> None:
        if self._release is not None:
            await self._release.wait()


class _FakeSsh:
    """Fake asyncssh.connect that records how many handshakes are in flight per host."""

    def __init__(
        self,
        release: asyncio.Event | None = None,
        expect_running: int = 0,
        fail: Callable[[], OSError] | None = None,
        fail_first: int | None = None,
    ) -> None:
        self.release = release
        self.fail = fail  # The error connects raise: for the first fail_first, or all if None
        self.fail_first = fail_first
        self.connects = 0
        self.in_flight: dict[str, int] = {}
        self.max_in_flight: dict[str, int] = {}
        self.running = [0]
        self.expect_running = expect_running
        self.all_running = asyncio.Event()

    async def connect(self, **kwargs: Any) -> Any:
        host = f"{kwargs['host'].lower()}:{kwargs['port']}"  # One sshd per address and port
        self.connects += 1
        self.in_flight[host] = self.in_flight.get(host, 0) + 1
        self.max_in_flight[host] = max(self.max_in_flight.get(host, 0), self.in_flight[host])
        await asyncio.sleep(0.01)  # The handshake
        self.in_flight[host] -= 1
        if self.fail and (self.fail_first is None or self.fail_first > 0):
            if self.fail_first is not None:
                self.fail_first -= 1
            raise self.fail()
        conn = AsyncMock()
        conn.__aenter__.return_value = conn

        def create_process(_command: str) -> _FakeProcess:
            return _FakeProcess(self.release, self.running, self)

        conn.create_process = create_process
        return conn


class TestSshHandshakeLimit:
    """sshd drops unauthenticated connections past MaxStartups, so handshakes are capped per host."""

    async def _run_many(
        self, fake: _FakeSsh, hosts: Sequence[str | Host], tmp_path: Path
    ) -> list[CommandResult]:
        known_hosts = tmp_path / "known_hosts"
        known_hosts.touch()
        with (
            patch("compose_farm.executor.SSH_KNOWN_HOSTS_PATH", known_hosts),
            patch("asyncssh.connect", side_effect=fake.connect),
        ):
            return await asyncio.gather(
                *(
                    _run_ssh_command(
                        host if isinstance(host, Host) else Host(address=host),
                        "true",
                        f"s{i}",
                        stream=False,
                    )
                    for i, host in enumerate(hosts)
                )
            )

    async def test_caps_concurrent_handshakes_per_host(self, tmp_path: Path) -> None:
        fake = _FakeSsh()
        results = await self._run_many(fake, ["10.0.0.1"] * 30 + ["10.0.0.2"] * 30, tmp_path)
        assert all(result.success for result in results)
        assert fake.max_in_flight == {"10.0.0.1:22": 8, "10.0.0.2:22": 8}

    async def test_limit_is_per_sshd(self, tmp_path: Path) -> None:
        """Address case doesn't split the budget; another port is another sshd."""
        hosts: list[str | Host] = [Host(address="Example.com")] * 15
        hosts += [Host(address="example.com")] * 15 + [Host(address="example.com", port=2222)] * 15
        fake = _FakeSsh()
        results = await self._run_many(fake, hosts, tmp_path)
        assert all(result.success for result in results)
        assert fake.max_in_flight == {"example.com:22": 8, "example.com:2222": 8}

    @pytest.mark.parametrize(
        "error",
        [
            lambda: ConnectionResetError(104, "Connection reset by peer"),  # sshd's MaxStartups
            lambda: ConnectionRefusedError(111, "Connection refused"),  # sshd not running
        ],
    )
    async def test_resets_and_refusals_dont_fail_queued_work(
        self, tmp_path: Path, error: Callable[[], OSError]
    ) -> None:
        fake = _FakeSsh(fail=error, fail_first=8)
        results = await self._run_many(fake, ["10.0.0.1"] * 20, tmp_path)
        assert [result.success for result in results].count(False) == 8
        assert fake.connects == 20
        assert fake.max_in_flight == {"10.0.0.1:22": 8}

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (lambda: TimeoutError(110, "Connection timed out"), "[Errno 110] Connection timed out"),
            (TimeoutError, "connecting to 10.0.0.1 timed out"),  # asyncio's: no message
            (
                lambda: OSError(113, "No route to host"),
                "[Errno 113] No route to host",
            ),  # LAN, via ARP
        ],
    )
    async def test_a_dead_host_fails_the_queued_handshakes(
        self, tmp_path: Path, error: Callable[[], OSError], message: str
    ) -> None:
        """A dead host fails after one round of connects, not one per batch of eight."""
        fake = _FakeSsh(fail=error)
        results = await self._run_many(fake, ["10.0.0.1"] * 20, tmp_path)
        assert {result.stderr for result in results} == {message}
        assert fake.connects == 8  # The 12 queued behind them never connected

    async def test_a_transient_timeout_doesnt_fail_queued_work(self, tmp_path: Path) -> None:
        """One connect timing out while others succeed means the host is up."""
        fake = _FakeSsh(fail=lambda: TimeoutError(110, "Connection timed out"), fail_first=1)
        results = await self._run_many(fake, ["10.0.0.1"] * 20, tmp_path)
        assert [result.success for result in results].count(False) == 1
        assert fake.connects == 20

    async def test_later_commands_try_again_after_a_dead_host(self, tmp_path: Path) -> None:
        """Only handshakes that were already queued fail fast; the host may be back."""
        fake = _FakeSsh(fail=lambda: TimeoutError(110, "Connection timed out"))
        await self._run_many(fake, ["10.0.0.1"] * 9, tmp_path)
        fake.fail = None
        results = await self._run_many(fake, ["10.0.0.1"] * 3, tmp_path)
        assert all(result.success for result in results)

    def test_finished_event_loops_are_released(self, tmp_path: Path) -> None:
        """A semaphore that had waiters references its loop; it must not keep the loop alive."""
        loops: list[weakref.ref[asyncio.AbstractEventLoop]] = []

        async def contended() -> None:
            loops.append(weakref.ref(asyncio.get_running_loop()))
            await self._run_many(_FakeSsh(), ["10.0.0.1"] * 20, tmp_path)

        asyncio.run(contended())
        gc.collect()
        assert loops[0]() is None

    async def test_does_not_cap_established_sessions(self, tmp_path: Path) -> None:
        """Long-running commands (cf logs -f) keep running side by side after their handshake."""
        release = asyncio.Event()
        fake = _FakeSsh(release, expect_running=20)
        task = asyncio.ensure_future(self._run_many(fake, ["10.0.0.1"] * 20, tmp_path))
        await asyncio.wait_for(fake.all_running.wait(), timeout=5)
        assert fake.running[0] == 20
        release.set()
        assert all(result.success for result in await task)


class TestBuildSshCommand:
    """Tests for native SSH command construction."""

    def test_requires_a_known_host_key(self) -> None:
        """Native SSH must reject unknown or changed server host keys."""
        args = build_ssh_command(Host(address="192.168.1.10"), "true")

        assert "StrictHostKeyChecking=yes" in args
        assert "StrictHostKeyChecking=no" not in args
        assert f"UserKnownHostsFile={SSH_KEY_PATH.parent / 'known_hosts'}" in args

    def test_uses_only_compose_farm_key_when_present(self, tmp_path: Path) -> None:
        """Native ssh should match asyncssh by not falling back to agent keys."""
        key_path = tmp_path / "id_ed25519"
        host = Host(address="192.168.1.10", user="me")

        with patch("compose_farm.executor.get_key_path", return_value=key_path):
            args = build_ssh_command(host, "true")

        assert args[0] == "ssh"
        assert "-i" in args
        assert str(key_path) in args
        assert "-o" in args
        assert "IdentitiesOnly=yes" in args


class TestRunCompose:
    """Tests for compose command execution."""

    async def test_run_compose_builds_correct_command(self, tmp_path: Path) -> None:
        # Create a minimal compose file
        compose_dir = tmp_path / "compose"
        stack_dir = compose_dir / "test-service"
        stack_dir.mkdir(parents=True)
        compose_file = stack_dir / "docker-compose.yml"
        compose_file.write_text("services: {}")

        config = Config(
            compose_dir=compose_dir,
            hosts={"local": Host(address="localhost")},
            stacks={"test-service": "local"},
        )

        # This will fail because docker compose isn't running,
        # but we can verify the command structure works
        result = await run_compose(config, "test-service", "config", stream=False)
        # Command may fail due to no docker, but structure is correct
        assert result.stack == "test-service"

    async def test_run_compose_uses_cd_pattern(self, tmp_path: Path) -> None:
        """Verify run_compose uses 'cd <dir> && docker compose' pattern."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"remote": Host(address="192.168.1.100")},
            stacks={"mystack": "remote"},
        )

        mock_result = CommandResult(stack="mystack", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            await run_compose(config, "mystack", "up -d", stream=False)

            # Verify the command uses cd pattern with quoted path
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            command = call_args[0][1]  # Second positional arg is command
            expected_dir = shlex.quote(str(tmp_path / "mystack"))
            assert command == f"cd {expected_dir} && docker compose up -d"

    async def test_run_compose_shell_quotes_stack_directory(self, tmp_path: Path) -> None:
        """Shell syntax in a config-derived directory must remain literal."""
        compose_dir = tmp_path / "$(touch PWNED)"
        config = Config(
            compose_dir=compose_dir,
            hosts={"remote": Host(address="192.168.1.100")},
            stacks={"mystack": "remote"},
        )

        mock_result = CommandResult(stack="mystack", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            await run_compose(config, "mystack", "up -d", stream=False)

        command = mock_run.call_args.args[1]
        expected_dir = shlex.quote(str(compose_dir / "mystack"))
        assert command == f"cd {expected_dir} && docker compose up -d"

    async def test_run_compose_works_without_local_compose_file(self, tmp_path: Path) -> None:
        """Verify compose works even when compose file doesn't exist locally.

        This is the bug from issue #162 - when running cf from a machine without
        NFS mounts, the compose file doesn't exist locally but should still work
        on the remote host.
        """
        config = Config(
            compose_dir=tmp_path,  # No compose files exist here
            hosts={"remote": Host(address="192.168.1.100")},
            stacks={"mystack": "remote"},
        )

        # Verify no compose file exists locally
        assert not (tmp_path / "mystack" / "compose.yaml").exists()
        assert not (tmp_path / "mystack" / "compose.yml").exists()

        mock_result = CommandResult(stack="mystack", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await run_compose(config, "mystack", "ps", stream=False)

            # Should succeed - docker compose on remote will find the file
            assert result.success
            # Command should use cd pattern, not -f with a specific file
            command = mock_run.call_args[0][1]
            assert "cd " in command
            assert " && docker compose " in command
            assert "-f " not in command  # Should NOT use -f flag
            assert mock_run.call_args.kwargs["host_name"] == "remote"
            assert mock_run.call_args.kwargs["label"] == "mystack"

    async def test_run_compose_on_host_uses_cd_pattern(self, tmp_path: Path) -> None:
        """Verify run_compose_on_host uses 'cd <dir> && docker compose' pattern."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"host1": Host(address="192.168.1.1")},
            stacks={"mystack": "host1"},
        )

        mock_result = CommandResult(stack="mystack", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await run_compose_on_host(config, "mystack", "host1", "down", stream=False)

            command = mock_run.call_args[0][1]
            expected_dir = shlex.quote(str(tmp_path / "mystack"))
            assert command == f"cd {expected_dir} && docker compose down"
            assert result.stack == "mystack"
            assert mock_run.call_args.kwargs["host_name"] == "host1"
            assert mock_run.call_args.kwargs["label"] == "mystack@host1"

    async def test_check_stack_running_uses_cd_pattern(self, tmp_path: Path) -> None:
        """Verify check_stack_running uses 'cd <dir> && docker compose' pattern."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"host1": Host(address="192.168.1.1")},
            stacks={"mystack": "host1"},
        )

        mock_result = CommandResult(stack="mystack", exit_code=0, success=True, stdout="abc123\n")
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            result = await check_stack_running(config, "mystack", "host1")

            assert result is True
            command = mock_run.call_args[0][1]
            expected_dir = shlex.quote(str(tmp_path / "mystack"))
            assert command == f"cd {expected_dir} && docker compose ps --status running -q"

    async def test_run_compose_quotes_paths_with_spaces(self, tmp_path: Path) -> None:
        """Verify paths with spaces are properly quoted."""
        compose_dir = tmp_path / "my compose dir"
        compose_dir.mkdir()

        config = Config(
            compose_dir=compose_dir,
            hosts={"remote": Host(address="192.168.1.100")},
            stacks={"my-stack": "remote"},
        )

        mock_result = CommandResult(stack="my-stack", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            await run_compose(config, "my-stack", "up -d", stream=False)

            command = mock_run.call_args[0][1]
            # Path should be quoted to handle spaces
            assert f"cd {shlex.quote(str(compose_dir / 'my-stack'))}" in command


class TestRunOnStacks:
    """Tests for parallel stack execution."""

    async def test_run_on_stacks_parallel(self) -> None:
        config = Config(
            compose_dir=Path("/tmp"),
            hosts={"local": Host(address="localhost")},
            stacks={"svc1": "local", "svc2": "local"},
        )

        # Use a simple command that will work without docker
        # We'll test the parallelism structure
        results = await run_on_stacks(config, ["svc1", "svc2"], "version", stream=False)
        assert len(results) == 2
        assert results[0].stack == "svc1"
        assert results[1].stack == "svc2"

    async def test_run_on_stacks_filter_host_limits_multi_host(self) -> None:
        """filter_host should only run on that host for multi-host stacks."""
        config = Config(
            compose_dir=Path("/tmp"),
            hosts={
                "host1": Host(address="192.168.1.1"),
                "host2": Host(address="192.168.1.2"),
                "host3": Host(address="192.168.1.3"),
            },
            stacks={"multi-svc": ["host1", "host2", "host3"]},  # multi-host stack
        )

        mock_result = CommandResult(stack="multi-svc", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            results = await run_on_stacks(
                config, ["multi-svc"], "down", stream=False, filter_host="host1"
            )

            # Should only call run_command once (for host1), not 3 times
            assert mock_run.call_count == 1
            assert mock_run.call_args.args[2] == "multi-svc"
            # Result should be for the filtered host
            assert len(results) == 1
            assert results[0].stack == "multi-svc"
            assert results[0].host == "host1"
            assert results[0].label == "multi-svc@host1"

    async def test_run_on_stacks_no_filter_runs_all_hosts(self) -> None:
        """Without filter_host, multi-host stacks run on all configured hosts."""
        config = Config(
            compose_dir=Path("/tmp"),
            hosts={
                "host1": Host(address="192.168.1.1"),
                "host2": Host(address="192.168.1.2"),
            },
            stacks={"multi-svc": ["host1", "host2"]},  # multi-host stack
        )

        mock_result = CommandResult(stack="multi-svc", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            results = await run_on_stacks(config, ["multi-svc"], "down", stream=False)

            # Should call run_command twice (once per host)
            assert mock_run.call_count == 2
            # Results should be for both hosts
            assert len(results) == 2

    async def test_run_on_stacks_multi_host_disables_raw_output(self) -> None:
        """Raw TTY output is unsafe when one stack fans out to multiple hosts."""
        config = Config(
            compose_dir=Path("/tmp"),
            hosts={
                "host1": Host(address="192.168.1.1"),
                "host2": Host(address="192.168.1.2"),
            },
            stacks={"glances": ["host1", "host2"]},
        )

        mock_result = CommandResult(stack="glances", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            await run_on_stacks(config, ["glances"], "up -d --pull always", raw=True)

        assert mock_run.call_count == 2
        assert all(call.kwargs["raw"] is False for call in mock_run.call_args_list)
        assert all(call.kwargs["stream"] is True for call in mock_run.call_args_list)

    async def test_run_on_stacks_multi_host_filter_keeps_raw_output(self) -> None:
        """A host filter reduces a multi-host stack to one compose process."""
        config = Config(
            compose_dir=Path("/tmp"),
            hosts={
                "host1": Host(address="192.168.1.1"),
                "host2": Host(address="192.168.1.2"),
            },
            stacks={"glances": ["host1", "host2"]},
        )

        mock_result = CommandResult(stack="glances", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_result
            await run_on_stacks(
                config,
                ["glances"],
                "up -d --pull always",
                raw=True,
                filter_host="host1",
            )

        mock_run.assert_awaited_once()
        assert mock_run.call_args.kwargs["raw"] is True


@linux_only
class TestCheckPathsExist:
    """Tests for check_paths_exist function (uses 'test -e' shell command)."""

    async def test_check_existing_paths(self, tmp_path: Path) -> None:
        """Check paths that exist."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )
        # Create test paths
        (tmp_path / "dir1").mkdir()
        (tmp_path / "file1").touch()

        result = await check_paths_exist(
            config, "local", [str(tmp_path / "dir1"), str(tmp_path / "file1")]
        )

        assert result[str(tmp_path / "dir1")] is True
        assert result[str(tmp_path / "file1")] is True

    async def test_check_missing_paths(self, tmp_path: Path) -> None:
        """Check paths that don't exist."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_paths_exist(
            config, "local", [str(tmp_path / "missing1"), str(tmp_path / "missing2")]
        )

        assert result[str(tmp_path / "missing1")] is False
        assert result[str(tmp_path / "missing2")] is False

    async def test_check_mixed_paths(self, tmp_path: Path) -> None:
        """Check mix of existing and missing paths."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )
        (tmp_path / "exists").mkdir()

        result = await check_paths_exist(
            config, "local", [str(tmp_path / "exists"), str(tmp_path / "missing")]
        )

        assert result[str(tmp_path / "exists")] is True
        assert result[str(tmp_path / "missing")] is False

    async def test_check_empty_paths(self, tmp_path: Path) -> None:
        """Empty path list returns empty dict."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_paths_exist(config, "local", [])
        assert result == {}

    async def test_remote_command_failure_raises(self, tmp_path: Path) -> None:
        """SSH/preflight failures should not look like every path is missing."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"remote": Host(address="192.168.1.10")},
            stacks={},
        )
        failed = CommandResult(
            stack="mount-check",
            exit_code=1,
            success=False,
            stderr="Permission denied for user basnijholt",
        )

        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = failed
            with pytest.raises(RemoteCheckError) as exc_info:
                await check_paths_exist(config, "remote", ["/opt/stacks"])

        assert "mount-check failed on remote" in str(exc_info.value)
        assert "Permission denied" in str(exc_info.value)

    async def test_remote_command_failure_retries_once(self, tmp_path: Path) -> None:
        """Transient SSH failures should not fail the whole preflight immediately."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"remote": Host(address="192.168.1.10")},
            stacks={},
        )
        failed = CommandResult(
            stack="mount-check",
            exit_code=1,
            success=False,
            stderr="Connection lost",
        )
        succeeded = CommandResult(
            stack="mount-check",
            exit_code=0,
            success=True,
            stdout="Y:/opt/stacks\n",
        )

        with patch("compose_farm.executor.run_command", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [failed, succeeded]
            result = await check_paths_exist(config, "remote", ["/opt/stacks"])

        assert result == {"/opt/stacks": True}
        assert mock_run.await_count == 2


@linux_only
class TestCheckNetworksExist:
    """Tests for check_networks_exist function (requires Docker)."""

    async def test_check_bridge_network_exists(self, tmp_path: Path) -> None:
        """The 'bridge' network always exists on Docker hosts."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(config, "local", ["bridge"])
        assert result["bridge"] is True

    async def test_check_nonexistent_network(self, tmp_path: Path) -> None:
        """Check a network that doesn't exist."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(config, "local", ["nonexistent_network_xyz_123"])
        assert result["nonexistent_network_xyz_123"] is False

    async def test_check_mixed_networks(self, tmp_path: Path) -> None:
        """Check mix of existing and non-existing networks."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(
            config, "local", ["bridge", "nonexistent_network_xyz_123"]
        )
        assert result["bridge"] is True
        assert result["nonexistent_network_xyz_123"] is False

    async def test_check_empty_networks(self, tmp_path: Path) -> None:
        """Empty network list returns empty dict."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await check_networks_exist(config, "local", [])
        assert result == {}


@linux_only
class TestGetRunningStacksOnHost:
    """Tests for get_running_stacks_on_host function (requires Docker)."""

    async def test_returns_set_of_stacks(self, tmp_path: Path) -> None:
        """Function returns a set of stack names."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        result = await get_running_stacks_on_host(config, "local")
        assert isinstance(result, set)

    async def test_filters_empty_lines(self, tmp_path: Path) -> None:
        """Empty project names are filtered out."""
        config = Config(
            compose_dir=tmp_path,
            hosts={"local": Host(address="localhost")},
            stacks={},
        )

        # Result should not contain empty strings
        result = await get_running_stacks_on_host(config, "local")
        assert "" not in result


class TestPluginComposeArgs:
    """Plugin compose_args reach every compose command, computed per host."""

    @staticmethod
    def _env_plugin() -> Recorder:
        plugin = Recorder({"args": ["--env-file", "/run/{host} {stack}.env"]})
        plugin.name = "env"
        return plugin

    def test_build_compose_command_quotes_extra_args(self) -> None:
        cmd = _build_compose_command(Path("/opt/x"), "up -d", ["--env-file", "/run/a b.env"])
        assert cmd == "cd /opt/x && docker compose --env-file '/run/a b.env' up -d"

    def test_build_compose_command_without_extra_args_is_unchanged(self) -> None:
        assert _build_compose_command(Path("/opt/x"), "ps") == "cd /opt/x && docker compose ps"

    async def test_run_compose_and_on_host(self, tmp_path: Path) -> None:
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), self._env_plugin())
        ok = CommandResult(stack="web", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", AsyncMock(return_value=ok)) as mock:
            await run_compose(cfg, "web", "ps")
            await run_compose_on_host(cfg, "web", "h2", "down")
        assert "--env-file '/run/h1 web.env' ps" in mock.call_args_list[0].args[1]
        assert "--env-file '/run/h2 web.env' down" in mock.call_args_list[1].args[1]

    async def test_multi_host_commands_are_per_host(self, tmp_path: Path) -> None:
        cfg = use_plugins(make_config(tmp_path, {"glances": "all"}), self._env_plugin())
        ok = CommandResult(stack="glances", exit_code=0, success=True)
        with patch("compose_farm.executor.run_command", AsyncMock(return_value=ok)) as mock:
            await run_on_stacks(cfg, ["glances"], "pull")
            await run_on_stacks(cfg, ["glances"], "pull", filter_host="h2")
        commands = [call.args[1] for call in mock.call_args_list]
        assert "'/run/h1 glances.env' pull" in commands[0]
        assert "'/run/h2 glances.env' pull" in commands[1]
        assert "'/run/h2 glances.env' pull" in commands[2]

    async def test_check_stack_running_uses_args(self, tmp_path: Path) -> None:
        cfg = use_plugins(make_config(tmp_path, {"web": "h1"}), self._env_plugin())
        ok = CommandResult(stack="web", exit_code=0, success=True, stdout="abc")
        with patch("compose_farm.executor.run_command", AsyncMock(return_value=ok)) as mock:
            assert await check_stack_running(cfg, "web", "h2")
        assert "'/run/h2 web.env' ps --status running -q" in mock.call_args.args[1]
