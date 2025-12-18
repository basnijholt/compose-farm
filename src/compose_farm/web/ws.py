"""WebSocket handler for terminal streaming."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import pty
import struct
import termios
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from compose_farm.executor import is_local
from compose_farm.web.app import get_config
from compose_farm.web.streaming import tasks

if TYPE_CHECKING:
    from compose_farm.config import Host

router = APIRouter()


def _parse_resize(msg: str) -> tuple[int, int] | None:
    """Parse a resize message, return (cols, rows) or None if not a resize."""
    try:
        data = json.loads(msg)
        if data.get("type") == "resize":
            return int(data["cols"]), int(data["rows"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass
    return None


def _resize_pty(fd: int, cols: int, rows: int) -> None:
    """Resize a local PTY."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


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
                _resize_pty(master_fd, *size)
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


async def _run_local_exec(websocket: WebSocket, exec_cmd: str) -> None:
    """Run docker exec locally with PTY."""
    master_fd, slave_fd = pty.openpty()

    proc = await asyncio.create_subprocess_shell(
        exec_cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    # Set non-blocking
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    await _bridge_websocket_to_fd(websocket, master_fd, proc)


async def _run_remote_exec(websocket: WebSocket, host: Host, exec_cmd: str) -> None:
    """Run docker exec on remote host via SSH with PTY."""
    async with asyncssh.connect(
        host.address,
        port=host.port,
        username=host.user,
        known_hosts=None,
    ) as conn:
        proc = await conn.create_process(
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
        await websocket.send_text(f"\x1b[31mHost '{host_name}' not found\x1b[0m\r\n")
        return

    exec_cmd = f"docker exec -it {container} /bin/sh -c 'command -v bash >/dev/null && exec bash || exec sh'"

    if is_local(host):
        await _run_local_exec(websocket, exec_cmd)
    else:
        await _run_remote_exec(websocket, host, exec_cmd)


@router.websocket("/ws/exec/{service}/{container}/{host}")
async def exec_websocket(
    websocket: WebSocket,
    service: str,  # noqa: ARG001
    container: str,
    host: str,
) -> None:
    """WebSocket endpoint for interactive container exec."""
    await websocket.accept()

    try:
        await websocket.send_text(f"\x1b[2m[Connecting to {container} on {host}...]\x1b[0m\r\n")
        await _run_exec_session(websocket, container, host)
        await websocket.send_text("\r\n\x1b[2m[Disconnected]\x1b[0m\r\n")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        with contextlib.suppress(Exception):
            await websocket.send_text(f"\x1b[31mError: {e}\x1b[0m\r\n")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/terminal/{task_id}")
async def terminal_websocket(websocket: WebSocket, task_id: str) -> None:
    """WebSocket endpoint for terminal streaming."""
    await websocket.accept()

    if task_id not in tasks:
        await websocket.send_text("\x1b[31mError: Task not found\x1b[0m\r\n")
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
                color = "32" if task["status"] == "completed" else "31"
                await websocket.send_text(f"\r\n\x1b[{color}m{status}\x1b[0m\r\n")
                await websocket.close()
                break

            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    finally:
        tasks.pop(task_id, None)
