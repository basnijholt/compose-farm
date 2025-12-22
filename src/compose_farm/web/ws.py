"""WebSocket handler for terminal streaming."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import pty
import shlex
import signal
import struct
import termios
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from compose_farm.executor import is_local, ssh_connect_kwargs
from compose_farm.web.deps import get_config
from compose_farm.web.streaming import CRLF, DIM, GREEN, RED, RESET, tasks

logger = logging.getLogger(__name__)

# Shell command to prefer bash over sh
SHELL_FALLBACK = "command -v bash >/dev/null && exec bash || exec sh"

if TYPE_CHECKING:
    from compose_farm.config import Host

router = APIRouter()


def _parse_resize(msg: str) -> tuple[int, int] | None:
    """Parse a resize message, return (cols, rows) or None if not a resize."""
    try:
        data = json.loads(msg)
        if isinstance(data, dict) and data.get("type") == "resize":
            return int(data["cols"]), int(data["rows"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass
    return None


def _resize_pty(
    fd: int, cols: int, rows: int, proc: asyncio.subprocess.Process | None = None
) -> None:
    """Resize a local PTY and send SIGWINCH to the process."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    # Explicitly send SIGWINCH so docker exec forwards it to the container
    if proc and proc.pid:
        os.kill(proc.pid, signal.SIGWINCH)


async def _bridge_websocket_to_fd(
    websocket: WebSocket,
    master_fd: int,
    proc: asyncio.subprocess.Process,
) -> None:
    """Bridge WebSocket to a local PTY file descriptor."""
    loop = asyncio.get_event_loop()

    async def read_output() -> None:
        while proc.returncode is None:
            try:
                data = await loop.run_in_executor(None, lambda: os.read(master_fd, 4096))
            except BlockingIOError:
                await asyncio.sleep(0.01)
                continue
            except OSError:
                break
            if not data:
                break
            await websocket.send_text(data.decode("utf-8", errors="replace"))

    read_task = asyncio.create_task(read_output())

    try:
        while proc.returncode is None:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
            except TimeoutError:
                continue
            if size := _parse_resize(msg):
                _resize_pty(master_fd, *size, proc)
            else:
                os.write(master_fd, msg.encode())
    finally:
        read_task.cancel()
        os.close(master_fd)
        if proc.returncode is None:
            proc.terminate()


async def _bridge_websocket_to_ssh(
    websocket: WebSocket,
    proc: Any,  # asyncssh.SSHClientProcess
) -> None:
    """Bridge WebSocket to an SSH process with PTY."""
    assert proc.stdout is not None
    assert proc.stdin is not None

    async def read_stdout() -> None:
        while proc.returncode is None:
            data = await proc.stdout.read(4096)
            if not data:
                break
            text = data if isinstance(data, str) else data.decode()
            await websocket.send_text(text)

    read_task = asyncio.create_task(read_stdout())

    try:
        while proc.returncode is None:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
            except TimeoutError:
                continue
            if size := _parse_resize(msg):
                proc.change_terminal_size(*size)
            else:
                proc.stdin.write(msg)
    finally:
        read_task.cancel()
        proc.terminate()


def _make_controlling_tty(slave_fd: int) -> None:
    """Set up the slave PTY as the controlling terminal for the child process."""
    # Create a new session
    os.setsid()
    # Make the slave fd the controlling terminal
    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)


async def _run_local_exec(websocket: WebSocket, argv: list[str]) -> None:
    """Run command locally with PTY using argv list (no shell interpretation)."""
    master_fd, slave_fd = pty.openpty()

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=lambda: _make_controlling_tty(slave_fd),
        start_new_session=False,  # We handle setsid in preexec_fn
    )
    os.close(slave_fd)

    # Set non-blocking
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    await _bridge_websocket_to_fd(websocket, master_fd, proc)


async def _run_remote_exec(
    websocket: WebSocket, host: Host, exec_cmd: str, *, agent_forwarding: bool = False
) -> None:
    """Run docker exec on remote host via SSH with PTY."""
    # ssh_connect_kwargs includes agent_path and client_keys fallback
    async with asyncssh.connect(
        **ssh_connect_kwargs(host),
        agent_forwarding=agent_forwarding,
    ) as conn:
        proc: asyncssh.SSHClientProcess[Any] = await conn.create_process(
            exec_cmd,
            term_type="xterm-256color",
            term_size=(80, 24),
        )
        async with proc:
            await _bridge_websocket_to_ssh(websocket, proc)


async def _run_exec_session(
    websocket: WebSocket,
    container: str,
    host_name: str,
) -> None:
    """Run an interactive docker exec session over WebSocket."""
    config = get_config()
    host = config.hosts.get(host_name)
    if not host:
        await websocket.send_text(f"{RED}Host '{host_name}' not found{RESET}{CRLF}")
        return

    if is_local(host):
        # Local: use argv list (no shell interpretation)
        argv = ["docker", "exec", "-it", container, "/bin/sh", "-c", SHELL_FALLBACK]
        await _run_local_exec(websocket, argv)
    else:
        # Remote: quote container name to prevent injection
        exec_cmd = (
            f"docker exec -it {shlex.quote(container)} /bin/sh -c {shlex.quote(SHELL_FALLBACK)}"
        )
        await _run_remote_exec(websocket, host, exec_cmd)


@router.websocket("/ws/exec/{stack}/{container}/{host}")
async def exec_websocket(
    websocket: WebSocket,
    stack: str,  # noqa: ARG001
    container: str,
    host: str,
) -> None:
    """WebSocket endpoint for interactive container exec."""
    await websocket.accept()

    try:
        await websocket.send_text(f"{DIM}[Connecting to {container} on {host}...]{RESET}{CRLF}")
        await _run_exec_session(websocket, container, host)
        await websocket.send_text(f"{CRLF}{DIM}[Disconnected]{RESET}{CRLF}")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket exec error for %s on %s", container, host)
        with contextlib.suppress(Exception):
            await websocket.send_text(f"{RED}Error: {e}{RESET}{CRLF}")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


async def _run_shell_session(
    websocket: WebSocket,
    host_name: str,
) -> None:
    """Run an interactive shell session on a host over WebSocket."""
    config = get_config()
    host = config.hosts.get(host_name)
    if not host:
        await websocket.send_text(f"{RED}Host '{host_name}' not found{RESET}{CRLF}")
        return

    # Start interactive shell in home directory
    shell_cmd = "cd ~ && exec bash -i || exec sh -i"

    if is_local(host):
        # Local: use argv list with shell -c to interpret the command
        argv = ["/bin/sh", "-c", shell_cmd]
        await _run_local_exec(websocket, argv)
    else:
        await _run_remote_exec(websocket, host, shell_cmd, agent_forwarding=True)


@router.websocket("/ws/shell/{host}")
async def shell_websocket(
    websocket: WebSocket,
    host: str,
) -> None:
    """WebSocket endpoint for interactive host shell access."""
    await websocket.accept()

    try:
        await websocket.send_text(f"{DIM}[Connecting to {host}...]{RESET}{CRLF}")
        await _run_shell_session(websocket, host)
        await websocket.send_text(f"{CRLF}{DIM}[Disconnected]{RESET}{CRLF}")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket shell error for host %s", host)
        with contextlib.suppress(Exception):
            await websocket.send_text(f"{RED}Error: {e}{RESET}{CRLF}")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/terminal/{task_id}")
async def terminal_websocket(websocket: WebSocket, task_id: str) -> None:
    """WebSocket endpoint for terminal streaming."""
    await websocket.accept()

    if task_id not in tasks:
        await websocket.send_text(
            f"{DIM}Task not found (expired or container restarted).{RESET}{CRLF}"
        )
        await websocket.close(code=4004)
        return

    task = tasks[task_id]
    sent_count = 0

    try:
        while True:
            # Send any new output
            while sent_count < len(task["output"]):
                await websocket.send_text(task["output"][sent_count])
                sent_count += 1

            if task["status"] in ("completed", "failed"):
                status = "[Done]" if task["status"] == "completed" else "[Failed]"
                color = GREEN if task["status"] == "completed" else RED
                await websocket.send_text(f"{CRLF}{color}{status}{RESET}{CRLF}")
                await websocket.close()
                break

            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    # Task stays in memory for reconnection; cleanup_stale_tasks() handles expiry
