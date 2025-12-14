"""Command execution via SSH or locally."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import asyncssh
from rich.console import Console
from rich.markup import escape

if TYPE_CHECKING:
    from .config import Config, Host

_console = Console(highlight=False)
_err_console = Console(stderr=True, highlight=False)

LOCAL_ADDRESSES = frozenset({"local", "localhost", "127.0.0.1", "::1"})


@lru_cache(maxsize=1)
def _get_local_ips() -> frozenset[str]:
    """Get all IP addresses of the current machine."""
    ips: set[str] = set()
    try:
        hostname = socket.gethostname()
        # Get all addresses for hostname
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0]
            if isinstance(addr, str):
                ips.add(addr)
        # Also try getting the default outbound IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return frozenset(ips)


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
    addr = host.address.lower()
    if addr in LOCAL_ADDRESSES:
        return True
    # Check if address matches any of this machine's IPs
    return addr in _get_local_ips()


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
                console = _err_console if is_stderr else _console
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    text = line.decode()
                    if text.strip():  # Skip empty lines
                        console.print(f"[cyan]\\[{prefix}][/] {escape(text)}", end="")

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
        _err_console.print(f"[cyan]\\[{service}][/] [red]Local error:[/] {e}")
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
                    console = _err_console if is_stderr else _console
                    async for line in reader:
                        if line.strip():  # Skip empty lines
                            console.print(f"[cyan]\\[{prefix}][/] {escape(line)}", end="")

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
        _err_console.print(f"[cyan]\\[{service}][/] [red]SSH error:[/] {e}")
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


async def run_compose_on_host(
    config: Config,
    service: str,
    host_name: str,
    compose_cmd: str,
    *,
    stream: bool = True,
) -> CommandResult:
    """Run a docker compose command for a service on a specific host.

    Used for migration - running 'down' on the old host before 'up' on new host.
    """
    host = config.hosts[host_name]
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


async def check_service_running(
    config: Config,
    service: str,
    host_name: str,
) -> bool:
    """Check if a service has running containers on a specific host."""
    host = config.hosts[host_name]
    compose_path = config.get_compose_path(service)

    # Use ps --status running to check for running containers
    command = f"docker compose -f {compose_path} ps --status running -q"
    result = await run_command(host, command, service, stream=False)

    # If command succeeded and has output, containers are running
    return result.success and bool(result.stdout.strip())


async def check_paths_exist(
    config: Config,
    host_name: str,
    paths: list[str],
) -> dict[str, bool]:
    """Check if multiple paths exist on a specific host.

    Returns a dict mapping path -> exists.
    """
    if not paths:
        return {}

    host = config.hosts[host_name]

    # Build a command that checks all paths efficiently
    # Using a subshell to check each path and report Y/N
    checks = []
    for p in paths:
        # Escape single quotes in path
        escaped = p.replace("'", "'\\''")
        checks.append(f"test -e '{escaped}' && echo 'Y:{escaped}' || echo 'N:{escaped}'")

    command = "; ".join(checks)
    result = await run_command(host, command, "mount-check", stream=False)

    exists: dict[str, bool] = dict.fromkeys(paths, False)
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("Y:"):
            exists[line[2:]] = True
        elif line.startswith("N:"):
            exists[line[2:]] = False

    return exists


async def check_networks_exist(
    config: Config,
    host_name: str,
    networks: list[str],
) -> dict[str, bool]:
    """Check if Docker networks exist on a specific host.

    Returns a dict mapping network_name -> exists.
    """
    if not networks:
        return {}

    host = config.hosts[host_name]

    # Check each network via docker network inspect
    checks = []
    for net in networks:
        escaped = net.replace("'", "'\\''")
        checks.append(
            f"docker network inspect '{escaped}' >/dev/null 2>&1 "
            f"&& echo 'Y:{escaped}' || echo 'N:{escaped}'"
        )

    command = "; ".join(checks)
    result = await run_command(host, command, "network-check", stream=False)

    exists: dict[str, bool] = dict.fromkeys(networks, False)
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("Y:"):
            exists[line[2:]] = True
        elif line.startswith("N:"):
            exists[line[2:]] = False

    return exists
