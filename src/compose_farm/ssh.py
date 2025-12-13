"""Command execution via SSH or locally."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import asyncssh

if TYPE_CHECKING:
    from .config import Config, Host

LOCAL_ADDRESSES = frozenset({"local", "localhost", "127.0.0.1", "::1"})


@dataclass
class CommandResult:
    """Result of a command execution."""

    service: str
    exit_code: int
    success: bool
    stdout: str = ""
    stderr: str = ""


def _is_local(host: Host) -> bool:
    """Check if host should run locally (no SSH)."""
    return host.address.lower() in LOCAL_ADDRESSES


async def _run_local_command(
    command: str,
    service: str,
    *,
    stream: bool = True,
) -> CommandResult:
    """Run a command locally with streaming output."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if stream and proc.stdout and proc.stderr:

            async def read_stream(
                reader: asyncio.StreamReader,
                prefix: str,
                *,
                is_stderr: bool = False,
            ) -> None:
                output = sys.stderr if is_stderr else sys.stdout
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    print(f"[{prefix}] {line.decode()}", end="", file=output, flush=True)

            await asyncio.gather(
                read_stream(proc.stdout, service),
                read_stream(proc.stderr, service, is_stderr=True),
            )

        stdout_data = b""
        stderr_data = b""
        if not stream:
            stdout_data, stderr_data = await proc.communicate()
        else:
            await proc.wait()

        return CommandResult(
            service=service,
            exit_code=proc.returncode or 0,
            success=proc.returncode == 0,
            stdout=stdout_data.decode() if stdout_data else "",
            stderr=stderr_data.decode() if stderr_data else "",
        )
    except OSError as e:
        print(f"[{service}] Local error: {e}", file=sys.stderr)
        return CommandResult(service=service, exit_code=1, success=False)


async def _run_ssh_command(
    host: Host,
    command: str,
    service: str,
    *,
    stream: bool = True,
) -> CommandResult:
    """Run a command on a remote host via SSH with streaming output."""
    proc: asyncssh.SSHClientProcess[Any]
    try:
        async with (
            asyncssh.connect(
                host.address,
                port=host.port,
                username=host.user,
                known_hosts=None,
            ) as conn,
            conn.create_process(command) as proc,
        ):
            if stream:

                async def read_stream(
                    reader: Any,
                    prefix: str,
                    *,
                    is_stderr: bool = False,
                ) -> None:
                    output = sys.stderr if is_stderr else sys.stdout
                    async for line in reader:
                        print(f"[{prefix}] {line}", end="", file=output, flush=True)

                await asyncio.gather(
                    read_stream(proc.stdout, service),
                    read_stream(proc.stderr, service, is_stderr=True),
                )

            stdout_data = ""
            stderr_data = ""
            if not stream:
                stdout_data = await proc.stdout.read()
                stderr_data = await proc.stderr.read()

            await proc.wait()
            return CommandResult(
                service=service,
                exit_code=proc.exit_status or 0,
                success=proc.exit_status == 0,
                stdout=stdout_data,
                stderr=stderr_data,
            )
    except (OSError, asyncssh.Error) as e:
        print(f"[{service}] SSH error: {e}", file=sys.stderr)
        return CommandResult(service=service, exit_code=1, success=False)


async def run_command(
    host: Host,
    command: str,
    service: str,
    *,
    stream: bool = True,
) -> CommandResult:
    """Run a command on a host (locally or via SSH)."""
    if _is_local(host):
        return await _run_local_command(command, service, stream=stream)
    return await _run_ssh_command(host, command, service, stream=stream)


async def run_compose(
    config: Config,
    service: str,
    compose_cmd: str,
    *,
    stream: bool = True,
) -> CommandResult:
    """Run a docker compose command for a service."""
    host = config.get_host(service)
    compose_path = config.get_compose_path(service)

    command = f"docker compose -f {compose_path} {compose_cmd}"
    return await run_command(host, command, service, stream=stream)


async def run_on_services(
    config: Config,
    services: list[str],
    compose_cmd: str,
    *,
    stream: bool = True,
) -> list[CommandResult]:
    """Run a docker compose command on multiple services in parallel."""
    tasks = [run_compose(config, service, compose_cmd, stream=stream) for service in services]
    return await asyncio.gather(*tasks)


async def run_sequential_commands(
    config: Config,
    service: str,
    commands: list[str],
    *,
    stream: bool = True,
) -> CommandResult:
    """Run multiple compose commands sequentially for a service."""
    for cmd in commands:
        result = await run_compose(config, service, cmd, stream=stream)
        if not result.success:
            return result
    return CommandResult(service=service, exit_code=0, success=True)


async def run_sequential_on_services(
    config: Config,
    services: list[str],
    commands: list[str],
    *,
    stream: bool = True,
) -> list[CommandResult]:
    """Run sequential commands on multiple services in parallel."""
    tasks = [
        run_sequential_commands(config, service, commands, stream=stream) for service in services
    ]
    return await asyncio.gather(*tasks)
