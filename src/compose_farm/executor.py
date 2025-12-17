"""Command execution via SSH or locally."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from rich.markup import escape

from .console import console, err_console

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import Config, Host

LOCAL_ADDRESSES = frozenset({"local", "localhost", "127.0.0.1", "::1"})
_DEFAULT_SSH_PORT = 22


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

    # SSH returns 255 when connection is closed unexpectedly (e.g., Ctrl+C)
    _SSH_CONNECTION_CLOSED = 255

    @property
    def interrupted(self) -> bool:
        """Check if command was killed by SIGINT (Ctrl+C)."""
        # Negative exit codes indicate signal termination; -2 = SIGINT
        return self.exit_code < 0 or self.exit_code == self._SSH_CONNECTION_CLOSED


def is_local(host: Host) -> bool:
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
    raw: bool = False,
) -> CommandResult:
    """Run a command locally with streaming output."""
    try:
        if raw:
            # Run with inherited stdout/stderr for proper \r handling
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=None,  # Inherit
                stderr=None,  # Inherit
            )
            await proc.wait()
            return CommandResult(
                service=service,
                exit_code=proc.returncode or 0,
                success=proc.returncode == 0,
            )

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
                out = err_console if is_stderr else console
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    text = line.decode()
                    if text.strip():  # Skip empty lines
                        out.print(f"[cyan]\\[{prefix}][/] {escape(text)}", end="")

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
        err_console.print(f"[cyan]\\[{service}][/] [red]Local error:[/] {e}")
        return CommandResult(service=service, exit_code=1, success=False)


async def _run_ssh_command(
    host: Host,
    command: str,
    service: str,
    *,
    stream: bool = True,
    raw: bool = False,
) -> CommandResult:
    """Run a command on a remote host via SSH with streaming output."""
    if raw:
        # Use native ssh with TTY for proper progress bar rendering
        ssh_args = ["ssh", "-t"]
        if host.port != _DEFAULT_SSH_PORT:
            ssh_args.extend(["-p", str(host.port)])
        ssh_args.extend([f"{host.user}@{host.address}", command])
        # Run in thread to avoid blocking the event loop
        result = await asyncio.to_thread(subprocess.run, ssh_args, check=False)
        return CommandResult(
            service=service,
            exit_code=result.returncode,
            success=result.returncode == 0,
        )

    import asyncssh  # noqa: PLC0415 - lazy import for faster CLI startup

    proc: asyncssh.SSHClientProcess[Any]
    try:
        async with asyncssh.connect(  # noqa: SIM117 - conn needed before create_process
            host.address,
            port=host.port,
            username=host.user,
            known_hosts=None,
        ) as conn:
            async with conn.create_process(command) as proc:
                if stream:

                    async def read_stream(
                        reader: Any,
                        prefix: str,
                        *,
                        is_stderr: bool = False,
                    ) -> None:
                        out = err_console if is_stderr else console
                        async for line in reader:
                            if line.strip():  # Skip empty lines
                                out.print(f"[cyan]\\[{prefix}][/] {escape(line)}", end="")

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
        err_console.print(f"[cyan]\\[{service}][/] [red]SSH error:[/] {e}")
        return CommandResult(service=service, exit_code=1, success=False)


async def run_command(
    host: Host,
    command: str,
    service: str,
    *,
    stream: bool = True,
    raw: bool = False,
) -> CommandResult:
    """Run a command on a host (locally or via SSH)."""
    if is_local(host):
        return await _run_local_command(command, service, stream=stream, raw=raw)
    return await _run_ssh_command(host, command, service, stream=stream, raw=raw)


async def run_compose(
    config: Config,
    service: str,
    compose_cmd: str,
    *,
    stream: bool = True,
    raw: bool = False,
) -> CommandResult:
    """Run a docker compose command for a service."""
    host = config.get_host(service)
    compose_path = config.get_compose_path(service)

    command = f"docker compose -f {compose_path} {compose_cmd}"
    return await run_command(host, command, service, stream=stream, raw=raw)


async def run_compose_on_host(
    config: Config,
    service: str,
    host_name: str,
    compose_cmd: str,
    *,
    stream: bool = True,
    raw: bool = False,
) -> CommandResult:
    """Run a docker compose command for a service on a specific host.

    Used for migration - running 'down' on the old host before 'up' on new host.
    """
    host = config.hosts[host_name]
    compose_path = config.get_compose_path(service)

    command = f"docker compose -f {compose_path} {compose_cmd}"
    return await run_command(host, command, service, stream=stream, raw=raw)


async def run_on_services(
    config: Config,
    services: list[str],
    compose_cmd: str,
    *,
    stream: bool = True,
    raw: bool = False,
) -> list[CommandResult]:
    """Run a docker compose command on multiple services in parallel.

    For multi-host services, runs on all configured hosts.
    Note: raw=True only makes sense for single-service operations.
    """
    return await run_sequential_on_services(config, services, [compose_cmd], stream=stream, raw=raw)


async def _run_sequential_commands(
    config: Config,
    service: str,
    commands: list[str],
    *,
    stream: bool = True,
    raw: bool = False,
) -> CommandResult:
    """Run multiple compose commands sequentially for a service."""
    for cmd in commands:
        result = await run_compose(config, service, cmd, stream=stream, raw=raw)
        if not result.success:
            return result
    return CommandResult(service=service, exit_code=0, success=True)


async def _run_sequential_commands_multi_host(
    config: Config,
    service: str,
    commands: list[str],
    *,
    stream: bool = True,
    raw: bool = False,
) -> list[CommandResult]:
    """Run multiple compose commands sequentially for a multi-host service.

    Commands are run sequentially, but each command runs on all hosts in parallel.
    """
    host_names = config.get_hosts(service)
    compose_path = config.get_compose_path(service)
    final_results: list[CommandResult] = []

    for cmd in commands:
        command = f"docker compose -f {compose_path} {cmd}"
        tasks = []
        for host_name in host_names:
            host = config.hosts[host_name]
            label = f"{service}@{host_name}" if len(host_names) > 1 else service
            tasks.append(run_command(host, command, label, stream=stream, raw=raw))

        results = await asyncio.gather(*tasks)
        final_results = list(results)

        # Check if any failed
        if any(not r.success for r in results):
            return final_results

    return final_results


async def run_sequential_on_services(
    config: Config,
    services: list[str],
    commands: list[str],
    *,
    stream: bool = True,
    raw: bool = False,
) -> list[CommandResult]:
    """Run sequential commands on multiple services in parallel.

    For multi-host services, runs on all configured hosts.
    Note: raw=True only makes sense for single-service operations.
    """
    # Separate multi-host and single-host services for type-safe gathering
    multi_host_tasks = []
    single_host_tasks = []

    for service in services:
        if config.is_multi_host(service):
            multi_host_tasks.append(
                _run_sequential_commands_multi_host(
                    config, service, commands, stream=stream, raw=raw
                )
            )
        else:
            single_host_tasks.append(
                _run_sequential_commands(config, service, commands, stream=stream, raw=raw)
            )

    # Gather results separately to maintain type safety
    flat_results: list[CommandResult] = []

    if multi_host_tasks:
        multi_results = await asyncio.gather(*multi_host_tasks)
        for result_list in multi_results:
            flat_results.extend(result_list)

    if single_host_tasks:
        single_results = await asyncio.gather(*single_host_tasks)
        flat_results.extend(single_results)

    return flat_results


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


async def _batch_check_existence(
    config: Config,
    host_name: str,
    items: list[str],
    cmd_template: Callable[[str], str],
    context: str,
) -> dict[str, bool]:
    """Check existence of multiple items on a host using a command template."""
    if not items:
        return {}

    host = config.hosts[host_name]
    checks = []
    for item in items:
        escaped = item.replace("'", "'\\''")
        checks.append(cmd_template(escaped))

    command = "; ".join(checks)
    result = await run_command(host, command, context, stream=False)

    exists: dict[str, bool] = dict.fromkeys(items, False)
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("Y:"):
            exists[line[2:]] = True
        elif line.startswith("N:"):
            exists[line[2:]] = False

    return exists


async def check_paths_exist(
    config: Config,
    host_name: str,
    paths: list[str],
) -> dict[str, bool]:
    """Check if multiple paths exist on a specific host.

    Returns a dict mapping path -> exists.
    Handles permission denied as "exists" (path is there, just not accessible).
    """
    # Only report missing if stat says "No such file", otherwise assume exists
    # (handles permission denied correctly - path exists, just not accessible)
    return await _batch_check_existence(
        config,
        host_name,
        paths,
        lambda esc: f"stat '{esc}' 2>&1 | grep -q 'No such file' && echo 'N:{esc}' || echo 'Y:{esc}'",
        "mount-check",
    )


async def check_networks_exist(
    config: Config,
    host_name: str,
    networks: list[str],
) -> dict[str, bool]:
    """Check if Docker networks exist on a specific host.

    Returns a dict mapping network_name -> exists.
    """
    return await _batch_check_existence(
        config,
        host_name,
        networks,
        lambda esc: (
            f"docker network inspect '{esc}' >/dev/null 2>&1 && echo 'Y:{esc}' || echo 'N:{esc}'"
        ),
        "network-check",
    )
