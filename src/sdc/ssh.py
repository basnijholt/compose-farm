"""SSH command execution with asyncssh."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import asyncssh

if TYPE_CHECKING:
    from .config import Config, Host


@dataclass
class CommandResult:
    """Result of a command execution."""

    service: str
    exit_code: int
    success: bool


async def run_ssh_command(
    host: Host,
    command: str,
    service: str,
    *,
    stream: bool = True,
) -> CommandResult:
    """Run a command on a remote host via SSH with streaming output."""
    try:
        async with asyncssh.connect(
            host.address,
            port=host.port,
            username=host.user,
            known_hosts=None,  # Use system known_hosts
        ) as conn:
            async with conn.create_process(command) as proc:
                if stream:

                    async def read_stream(
                        stream: asyncssh.SSHReader,
                        prefix: str,
                        is_stderr: bool = False,
                    ) -> None:
                        output = sys.stderr if is_stderr else sys.stdout
                        async for line in stream:
                            print(f"[{prefix}] {line}", end="", file=output, flush=True)

                    await asyncio.gather(
                        read_stream(proc.stdout, service),
                        read_stream(proc.stderr, service, is_stderr=True),
                    )

                await proc.wait()
                return CommandResult(
                    service=service,
                    exit_code=proc.exit_status or 0,
                    success=proc.exit_status == 0,
                )
    except (OSError, asyncssh.Error) as e:
        print(f"[{service}] SSH error: {e}", file=sys.stderr)
        return CommandResult(service=service, exit_code=1, success=False)


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
    return await run_ssh_command(host, command, service, stream=stream)


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
        run_sequential_commands(config, service, commands, stream=stream)
        for service in services
    ]
    return await asyncio.gather(*tasks)
