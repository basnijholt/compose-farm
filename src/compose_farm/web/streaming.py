"""Streaming executor adapter for web UI."""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from compose_farm.executor import build_ssh_command
from compose_farm.ssh_keys import get_ssh_auth_sock

if TYPE_CHECKING:
    from compose_farm.config import Config

# Environment variable to identify the web stack (for self-update detection)
CF_WEB_STACK = os.environ.get("CF_WEB_STACK", "")

# ANSI escape codes for terminal output
RED = "\x1b[31m"
GREEN = "\x1b[32m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
CRLF = "\r\n"

# In-memory task registry
tasks: dict[str, dict[str, Any]] = {}

# How long to keep completed tasks (10 minutes)
TASK_TTL_SECONDS = 600


def cleanup_stale_tasks() -> int:
    """Remove tasks that completed more than TASK_TTL_SECONDS ago.

    Returns the number of tasks removed.
    """
    cutoff = time.time() - TASK_TTL_SECONDS
    stale = [
        tid
        for tid, task in tasks.items()
        if task.get("completed_at") and task["completed_at"] < cutoff
    ]
    for tid in stale:
        tasks.pop(tid, None)
    return len(stale)


async def stream_to_task(task_id: str, message: str) -> None:
    """Send a message to a task's output buffer."""
    if task_id in tasks:
        tasks[task_id]["output"].append(message)


async def _stream_subprocess(task_id: str, args: list[str], env: dict[str, str]) -> int:
    """Run subprocess and stream output to task buffer. Returns exit code."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    if process.stdout:
        async for line in process.stdout:
            text = line.decode("utf-8", errors="replace")
            # Convert \n to \r\n for xterm.js
            if text.endswith("\n") and not text.endswith("\r\n"):
                text = text[:-1] + "\r\n"
            await stream_to_task(task_id, text)
    return await process.wait()


async def run_cli_streaming(
    config: Config,
    args: list[str],
    task_id: str,
) -> None:
    """Run a cf CLI command as subprocess and stream output to task buffer."""
    try:
        cmd = ["cf", *args, f"--config={config.config_path}"]
        await stream_to_task(task_id, f"{DIM}$ {' '.join(['cf', *args])}{RESET}{CRLF}")

        # Build environment with color support and SSH agent
        env = {**os.environ, "FORCE_COLOR": "1", "TERM": "xterm-256color", "COLUMNS": "120"}
        if ssh_sock := get_ssh_auth_sock():
            env["SSH_AUTH_SOCK"] = ssh_sock

        exit_code = await _stream_subprocess(task_id, cmd, env)
        tasks[task_id]["status"] = "completed" if exit_code == 0 else "failed"
        tasks[task_id]["completed_at"] = time.time()

    except Exception as e:
        await stream_to_task(task_id, f"{RED}Error: {e}{RESET}{CRLF}")
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["completed_at"] = time.time()


def _is_self_update(stack: str, command: str) -> bool:
    """Check if this is a self-update (updating the web stack itself).

    Self-updates need special handling because running 'down' on the container
    we're running in would kill the process before 'up' can execute.
    """
    if not CF_WEB_STACK or stack != CF_WEB_STACK:
        return False
    # Commands that involve 'down' need SSH: update, restart, down
    return command in ("update", "restart", "down")


async def _run_cli_via_ssh(
    config: Config,
    args: list[str],
    task_id: str,
) -> None:
    """Run a cf CLI command via SSH for self-updates (survives container restart)."""
    try:
        host = config.get_host(CF_WEB_STACK)
        cf_cmd = f"cf {' '.join(args)} --config={config.config_path}"
        # Include task_id to prevent collision with concurrent updates
        log_file = f"/tmp/cf-self-update-{task_id}.log"  # noqa: S108

        # setsid detaches command; tail streams output until SSH dies
        remote_cmd = (
            f"rm -f {log_file} && "
            f"PATH=$HOME/.local/bin:/usr/local/bin:$PATH "
            f"setsid sh -c '{cf_cmd} > {log_file} 2>&1' & "
            f"sleep 0.3 && tail -f {log_file} 2>/dev/null"
        )

        await stream_to_task(task_id, f"{DIM}$ {cf_cmd}{RESET}{CRLF}")
        await stream_to_task(task_id, f"{GREEN}Running via SSH (detached with setsid){RESET}{CRLF}")

        ssh_args = build_ssh_command(host, remote_cmd, tty=False)
        env = {**os.environ}
        if ssh_sock := get_ssh_auth_sock():
            env["SSH_AUTH_SOCK"] = ssh_sock

        exit_code = await _stream_subprocess(task_id, ssh_args, env)

        # Exit code 255 = SSH closed (container died during down) - expected for self-updates
        if exit_code == 255:  # noqa: PLR2004
            await stream_to_task(
                task_id,
                f"{CRLF}{GREEN}Container restarting... refresh the page in a few seconds.{RESET}{CRLF}",
            )
            tasks[task_id]["status"] = "completed"
        else:
            tasks[task_id]["status"] = "completed" if exit_code == 0 else "failed"
        tasks[task_id]["completed_at"] = time.time()

    except Exception as e:
        await stream_to_task(task_id, f"{RED}Error: {e}{RESET}{CRLF}")
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["completed_at"] = time.time()


async def run_compose_streaming(
    config: Config,
    stack: str,
    command: str,
    task_id: str,
) -> None:
    """Run a compose command (up/down/pull/restart) via CLI subprocess."""
    # Split command into args (e.g., "up -d" -> ["up", "-d"])
    args = command.split()
    cli_cmd = args[0]  # up, down, pull, restart
    extra_args = args[1:]  # -d, etc.

    # Build CLI args
    cli_args = [cli_cmd, stack, *extra_args]

    # Use SSH for self-updates to survive container restart
    if _is_self_update(stack, cli_cmd):
        await _run_cli_via_ssh(config, cli_args, task_id)
    else:
        await run_cli_streaming(config, cli_args, task_id)
