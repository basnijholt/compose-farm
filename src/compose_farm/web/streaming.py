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

# Environment variable to identify the web service (for self-update detection)
CF_WEB_SERVICE = os.environ.get("CF_WEB_SERVICE", "")

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


async def run_cli_streaming(
    config: Config,
    args: list[str],
    task_id: str,
) -> None:
    """Run a cf CLI command as subprocess and stream output to task buffer.

    This reuses all CLI logic including Rich formatting, progress bars, etc.
    The subprocess gets a pseudo-TTY via FORCE_COLOR so Rich outputs ANSI codes.
    """
    try:
        # Build command - config option goes after the subcommand
        cmd = ["cf", *args, f"--config={config.config_path}"]

        # Show command being executed
        cmd_display = " ".join(["cf", *args])
        await stream_to_task(task_id, f"{DIM}$ {cmd_display}{RESET}{CRLF}")

        # Force color output even though there's no real TTY
        # Set COLUMNS for Rich/Typer to format output correctly
        env = {"FORCE_COLOR": "1", "TERM": "xterm-256color", "COLUMNS": "120"}

        # Ensure SSH agent is available (auto-detect if needed)
        ssh_sock = get_ssh_auth_sock()
        if ssh_sock:
            env["SSH_AUTH_SOCK"] = ssh_sock

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, **env},
        )

        # Stream output line by line
        if process.stdout:
            async for line in process.stdout:
                text = line.decode("utf-8", errors="replace")
                # Convert \n to \r\n for xterm.js
                if text.endswith("\n") and not text.endswith("\r\n"):
                    text = text[:-1] + "\r\n"
                await stream_to_task(task_id, text)

        exit_code = await process.wait()
        tasks[task_id]["status"] = "completed" if exit_code == 0 else "failed"
        tasks[task_id]["completed_at"] = time.time()

    except Exception as e:
        await stream_to_task(task_id, f"{RED}Error: {e}{RESET}{CRLF}")
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["completed_at"] = time.time()


def _is_self_update(service: str, command: str) -> bool:
    """Check if this is a self-update (updating the web service itself).

    Self-updates need special handling because running 'down' on the container
    we're running in would kill the process before 'up' can execute.
    """
    if not CF_WEB_SERVICE or service != CF_WEB_SERVICE:
        return False
    # Commands that involve 'down' need SSH: update, restart, down
    return command in ("update", "restart", "down")


async def _run_cli_via_ssh(
    config: Config,
    args: list[str],
    task_id: str,
) -> None:
    """Run a cf CLI command via SSH to the host.

    Used for self-updates to ensure the command survives container restart.
    The command is run with nohup in the background - we don't stream output
    since the SSH connection will die when the container is killed.
    """
    try:
        # Get the host for the web service
        host = config.get_host(CF_WEB_SERVICE)

        # Build the remote command with nohup to survive SSH disconnect.
        # Fire-and-forget: we can't stream output because SSH dies with container.
        cf_cmd = f"cf {' '.join(args)} --config={config.config_path}"
        remote_cmd = (
            f"PATH=$HOME/.local/bin:/usr/local/bin:$PATH && nohup {cf_cmd} > /dev/null 2>&1 &"
        )

        # Show what we're doing
        await stream_to_task(
            task_id,
            f"{DIM}$ {cf_cmd}{RESET}{CRLF}",
        )
        await stream_to_task(
            task_id,
            f"{GREEN}Starting self-update via SSH...{RESET}{CRLF}",
        )

        # Build SSH command
        ssh_args = build_ssh_command(host, remote_cmd, tty=False)

        # Set up environment with SSH agent
        env = {**os.environ}
        ssh_sock = get_ssh_auth_sock()
        if ssh_sock:
            env["SSH_AUTH_SOCK"] = ssh_sock

        process = await asyncio.create_subprocess_exec(
            *ssh_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

        await process.wait()

        # Command was started successfully (nohup backgrounded it)
        await stream_to_task(
            task_id,
            f"{GREEN}Update started. Container will restart shortly.{RESET}{CRLF}",
        )
        await stream_to_task(
            task_id,
            f"{DIM}Refresh the page in a few seconds...{RESET}{CRLF}",
        )
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["completed_at"] = time.time()

    except Exception as e:
        await stream_to_task(task_id, f"{RED}Error: {e}{RESET}{CRLF}")
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["completed_at"] = time.time()


async def run_compose_streaming(
    config: Config,
    service: str,
    command: str,
    task_id: str,
) -> None:
    """Run a compose command (up/down/pull/restart) via CLI subprocess."""
    # Split command into args (e.g., "up -d" -> ["up", "-d"])
    args = command.split()
    cli_cmd = args[0]  # up, down, pull, restart
    extra_args = args[1:]  # -d, etc.

    # Build CLI args
    cli_args = [cli_cmd, service, *extra_args]

    # Use SSH for self-updates to survive container restart
    if _is_self_update(service, cli_cmd):
        await _run_cli_via_ssh(config, cli_args, task_id)
    else:
        await run_cli_streaming(config, cli_args, task_id)
