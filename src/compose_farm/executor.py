"""Command execution via SSH or locally."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from rich.markup import escape

from .console import console, err_console
from .ssh_keys import get_key_path, get_ssh_auth_sock, get_ssh_env

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import Config, Host

LOCAL_ADDRESSES = frozenset({"local", "localhost", "127.0.0.1", "::1"})
_DEFAULT_SSH_PORT = 22


class TTLCache:
    """Simple TTL cache for async function results."""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        """Initialize cache with default TTL in seconds."""
        # Cache stores: key -> (timestamp, value, item_ttl)
        self._cache: dict[str, tuple[float, Any, float]] = {}
        self._default_ttl = ttl_seconds

    def get(self, key: str) -> Any | None:
        """Get value if exists and not expired."""
        if key in self._cache:
            timestamp, value, item_ttl = self._cache[key]
            if time.monotonic() - timestamp < item_ttl:
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        """Set value with current timestamp and optional custom TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        self._cache[key] = (time.monotonic(), value, ttl)

    def clear(self) -> None:
        """Clear all cached values."""
        self._cache.clear()


# Cache compose labels per host for 30 seconds
_compose_labels_cache = TTLCache(ttl_seconds=30.0)


def _print_compose_command(
    host_name: str,
    compose_dir: str,
    compose_path: str,
    compose_cmd: str,
) -> None:
    """Print the docker compose command being executed.

    Shows the host and a simplified command with relative path from compose_dir.
    """
    # Show relative path from compose_dir for cleaner output
    if compose_path.startswith(compose_dir):
        rel_path = compose_path[len(compose_dir) :].lstrip("/")
    else:
        rel_path = compose_path

    console.print(
        f"[dim][magenta]{host_name}[/magenta]: docker compose -f {rel_path} {compose_cmd}[/dim]"
    )


async def _stream_output_lines(
    reader: Any,
    prefix: str,
    *,
    is_stderr: bool = False,
) -> None:
    """Stream lines from a reader to console with a stack prefix.

    Works with both asyncio.StreamReader (bytes) and asyncssh readers (str).
    If prefix is empty, output is printed without a prefix.
    """
    out = err_console if is_stderr else console
    async for line in reader:
        text = line.decode() if isinstance(line, bytes) else line
        if text.strip():
            if prefix:
                out.print(f"[cyan]\\[{prefix}][/] {escape(text)}", end="")
            else:
                out.print(escape(text), end="")


def build_ssh_command(host: Host, command: str, *, tty: bool = False) -> list[str]:
    """Build SSH command args for executing a command on a remote host.

    Args:
        host: Host configuration with address, port, user
        command: Command to run on the remote host
        tty: Whether to allocate a TTY (for interactive/progress bar commands)

    Returns:
        List of command args suitable for subprocess

    """
    ssh_args = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
    ]
    if tty:
        ssh_args.insert(1, "-tt")  # Force TTY allocation

    key_path = get_key_path()
    if key_path:
        ssh_args.extend(["-i", str(key_path)])

    if host.port != _DEFAULT_SSH_PORT:
        ssh_args.extend(["-p", str(host.port)])

    ssh_args.append(f"{host.user}@{host.address}")
    ssh_args.append(command)

    return ssh_args


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

    stack: str
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


def ssh_connect_kwargs(host: Host) -> dict[str, Any]:
    """Get kwargs for asyncssh.connect() from a Host config."""
    kwargs: dict[str, Any] = {
        "host": host.address,
        "port": host.port,
        "username": host.user,
        "known_hosts": None,
        "gss_auth": False,  # Disable GSSAPI - causes multi-second delays
    }
    # Add key file fallback (prioritized over agent if present)
    key_path = get_key_path()
    agent_path = get_ssh_auth_sock()

    if key_path:
        # If dedicated key exists, force use of it and ignore agent
        # This avoids issues with stale/broken forwarded agents in Docker
        kwargs["client_keys"] = [str(key_path)]
    elif agent_path:
        # Fallback to agent if no dedicated key
        kwargs["agent_path"] = agent_path

    return kwargs


async def _run_local_command(
    command: str,
    stack: str,
    *,
    stream: bool = True,
    raw: bool = False,
    prefix: str = "",
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
                stack=stack,
                exit_code=proc.returncode or 0,
                success=proc.returncode == 0,
            )

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if stream and proc.stdout and proc.stderr:
            await asyncio.gather(
                _stream_output_lines(proc.stdout, prefix),
                _stream_output_lines(proc.stderr, prefix, is_stderr=True),
            )

        stdout_data = b""
        stderr_data = b""
        if not stream:
            stdout_data, stderr_data = await proc.communicate()
        else:
            await proc.wait()

        return CommandResult(
            stack=stack,
            exit_code=proc.returncode or 0,
            success=proc.returncode == 0,
            stdout=stdout_data.decode() if stdout_data else "",
            stderr=stderr_data.decode() if stderr_data else "",
        )
    except OSError as e:
        err_console.print(f"[cyan]\\[{stack}][/] [red]Local error:[/] {e}")
        return CommandResult(stack=stack, exit_code=1, success=False)


async def _run_ssh_command(
    host: Host,
    command: str,
    stack: str,
    *,
    stream: bool = True,
    raw: bool = False,
    prefix: str = "",
) -> CommandResult:
    """Run a command on a remote host via SSH with streaming output."""
    if raw:
        # Use native ssh with TTY for proper progress bar rendering
        ssh_args = build_ssh_command(host, command, tty=True)

        def run_ssh() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(ssh_args, check=False, env=get_ssh_env())

        # Run in thread to avoid blocking the event loop
        # Use get_ssh_env() to auto-detect SSH agent socket
        result = await asyncio.to_thread(run_ssh)
        return CommandResult(
            stack=stack,
            exit_code=result.returncode,
            success=result.returncode == 0,
        )

    import asyncssh  # noqa: PLC0415 - lazy import for faster CLI startup

    proc: asyncssh.SSHClientProcess[Any]
    try:
        async with asyncssh.connect(**ssh_connect_kwargs(host)) as conn:  # noqa: SIM117
            async with conn.create_process(command) as proc:
                if stream:
                    await asyncio.gather(
                        _stream_output_lines(proc.stdout, prefix),
                        _stream_output_lines(proc.stderr, prefix, is_stderr=True),
                    )

                stdout_data = ""
                stderr_data = ""
                if not stream:
                    stdout_data = await proc.stdout.read()
                    stderr_data = await proc.stderr.read()

                await proc.wait()
                return CommandResult(
                    stack=stack,
                    exit_code=proc.exit_status or 0,
                    success=proc.exit_status == 0,
                    stdout=stdout_data,
                    stderr=stderr_data,
                )
    except (OSError, asyncssh.Error) as e:
        err_console.print(f"[cyan]\\[{stack}][/] [red]SSH error:[/] {e}")
        return CommandResult(stack=stack, exit_code=1, success=False)


async def run_command(
    host: Host,
    command: str,
    stack: str,
    *,
    stream: bool = True,
    raw: bool = False,
    prefix: str | None = None,
) -> CommandResult:
    """Run a command on a host (locally or via SSH).

    Args:
        host: Host configuration
        command: Command to run
        stack: Stack name (stored in result)
        stream: Whether to stream output (default True)
        raw: Whether to use raw mode with TTY (default False)
        prefix: Output prefix. None=use stack name, ""=no prefix.

    """
    output_prefix = stack if prefix is None else prefix
    if is_local(host):
        return await _run_local_command(
            command, stack, stream=stream, raw=raw, prefix=output_prefix
        )
    return await _run_ssh_command(
        host, command, stack, stream=stream, raw=raw, prefix=output_prefix
    )


async def run_compose(
    config: Config,
    stack: str,
    compose_cmd: str,
    *,
    stream: bool = True,
    raw: bool = False,
    prefix: str | None = None,
) -> CommandResult:
    """Run a docker compose command for a stack."""
    host_name = config.get_hosts(stack)[0]
    host = config.hosts[host_name]
    compose_path = config.get_compose_path(stack)

    _print_compose_command(host_name, str(config.compose_dir), str(compose_path), compose_cmd)

    command = f"docker compose -f {compose_path} {compose_cmd}"
    return await run_command(host, command, stack, stream=stream, raw=raw, prefix=prefix)


async def run_compose_on_host(
    config: Config,
    stack: str,
    host_name: str,
    compose_cmd: str,
    *,
    stream: bool = True,
    raw: bool = False,
    prefix: str | None = None,
) -> CommandResult:
    """Run a docker compose command for a stack on a specific host.

    Used for migration - running 'down' on the old host before 'up' on new host.
    """
    host = config.hosts[host_name]
    compose_path = config.get_compose_path(stack)

    _print_compose_command(host_name, str(config.compose_dir), str(compose_path), compose_cmd)

    command = f"docker compose -f {compose_path} {compose_cmd}"
    return await run_command(host, command, stack, stream=stream, raw=raw, prefix=prefix)


async def run_on_stacks(
    config: Config,
    stacks: list[str],
    compose_cmd: str,
    *,
    stream: bool = True,
    raw: bool = False,
) -> list[CommandResult]:
    """Run a docker compose command on multiple stacks in parallel.

    For multi-host stacks, runs on all configured hosts.
    Note: raw=True only makes sense for single-stack operations.
    """
    return await run_sequential_on_stacks(config, stacks, [compose_cmd], stream=stream, raw=raw)


async def _run_sequential_stack_commands(
    config: Config,
    stack: str,
    commands: list[str],
    *,
    stream: bool = True,
    raw: bool = False,
    prefix: str | None = None,
) -> CommandResult:
    """Run multiple compose commands sequentially for a stack."""
    for cmd in commands:
        result = await run_compose(config, stack, cmd, stream=stream, raw=raw, prefix=prefix)
        if not result.success:
            return result
    return CommandResult(stack=stack, exit_code=0, success=True)


async def _run_sequential_stack_commands_multi_host(
    config: Config,
    stack: str,
    commands: list[str],
    *,
    stream: bool = True,
    raw: bool = False,
    prefix: str | None = None,
) -> list[CommandResult]:
    """Run multiple compose commands sequentially for a multi-host stack.

    Commands are run sequentially, but each command runs on all hosts in parallel.
    For multi-host stacks, prefix defaults to stack@host format.
    """
    host_names = config.get_hosts(stack)
    compose_path = config.get_compose_path(stack)
    final_results: list[CommandResult] = []

    for cmd in commands:
        command = f"docker compose -f {compose_path} {cmd}"
        tasks = []
        for host_name in host_names:
            _print_compose_command(host_name, str(config.compose_dir), str(compose_path), cmd)
            host = config.hosts[host_name]
            # For multi-host stacks, always use stack@host prefix to distinguish output
            label = f"{stack}@{host_name}" if len(host_names) > 1 else stack
            # Multi-host stacks always need prefixes to distinguish output from different hosts
            # (ignore empty prefix from single-stack batches - we still need to distinguish hosts)
            effective_prefix = label if len(host_names) > 1 else prefix
            tasks.append(
                run_command(host, command, label, stream=stream, raw=raw, prefix=effective_prefix)
            )

        results = await asyncio.gather(*tasks)
        final_results = list(results)

        # Check if any failed
        if any(not r.success for r in results):
            return final_results

    return final_results


async def run_sequential_on_stacks(
    config: Config,
    stacks: list[str],
    commands: list[str],
    *,
    stream: bool = True,
    raw: bool = False,
) -> list[CommandResult]:
    """Run sequential commands on multiple stacks in parallel.

    For multi-host stacks, runs on all configured hosts.
    Note: raw=True only makes sense for single-stack operations.
    """
    # Skip prefix for single-stack operations (command line already shows context)
    prefix: str | None = "" if len(stacks) == 1 else None

    # Separate multi-host and single-host stacks for type-safe gathering
    multi_host_tasks = []
    single_host_tasks = []

    for stack in stacks:
        if config.is_multi_host(stack):
            multi_host_tasks.append(
                _run_sequential_stack_commands_multi_host(
                    config, stack, commands, stream=stream, raw=raw, prefix=prefix
                )
            )
        else:
            single_host_tasks.append(
                _run_sequential_stack_commands(
                    config, stack, commands, stream=stream, raw=raw, prefix=prefix
                )
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


async def check_stack_running(
    config: Config,
    stack: str,
    host_name: str,
) -> bool:
    """Check if a stack has running containers on a specific host."""
    host = config.hosts[host_name]
    compose_path = config.get_compose_path(stack)

    # Use ps --status running to check for running containers
    command = f"docker compose -f {compose_path} ps --status running -q"
    result = await run_command(host, command, stack, stream=False)

    # If command succeeded and has output, containers are running
    return result.success and bool(result.stdout.strip())


async def get_running_stacks_on_host(
    config: Config,
    host_name: str,
) -> set[str]:
    """Get all running compose stacks on a host in a single SSH call.

    Uses docker ps with the compose.project label to identify running stacks.
    Much more efficient than checking each stack individually.
    """
    host = config.hosts[host_name]

    # Get unique project names from running containers
    command = "docker ps --format '{{.Label \"com.docker.compose.project\"}}' | sort -u"
    result = await run_command(host, command, stack=host_name, stream=False, prefix="")

    if not result.success:
        return set()

    # Filter out empty lines and return as set
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


async def get_container_compose_labels(
    config: Config,
    host_name: str,
) -> dict[str, tuple[str, str]]:
    """Get compose labels for all containers on a host.

    Returns dict of container_name -> (project, service).
    Includes all containers (-a flag) since Glances shows stopped containers too.
    Falls back to empty dict on timeout/error (5s timeout).
    Results are cached for 30 seconds to reduce SSH overhead.
    """
    # Check cache first
    cached: dict[str, tuple[str, str]] | None = _compose_labels_cache.get(host_name)
    if cached is not None:
        return cached

    host = config.hosts[host_name]
    cmd = (
        "docker ps -a --format "
        '\'{{.Names}}\t{{.Label "com.docker.compose.project"}}\t'
        '{{.Label "com.docker.compose.service"}}\''
    )

    try:
        async with asyncio.timeout(5.0):
            result = await run_command(host, cmd, stack=host_name, stream=False, prefix="")
    except TimeoutError:
        return {}
    except Exception:
        return {}

    labels: dict[str, tuple[str, str]] = {}
    if result.success:
        for line in result.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 3:  # noqa: PLR2004
                name, project, service = parts[0], parts[1], parts[2]
                labels[name] = (project or "", service or "")

    # Cache the result
    _compose_labels_cache.set(host_name, labels)
    return labels


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
