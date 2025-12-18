"""Streaming executor adapter for web UI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from compose_farm.config import Config

# ANSI escape codes for terminal output
RED = "\x1b[31m"
GREEN = "\x1b[32m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
CRLF = "\r\n"


def _get_ssh_auth_sock() -> str | None:
    """Get SSH_AUTH_SOCK, auto-detecting forwarded agent if needed."""
    sock = os.environ.get("SSH_AUTH_SOCK")
    if sock and Path(sock).is_socket():
        return sock

    # Try to find a forwarded SSH agent socket
    agent_dir = Path.home() / ".ssh" / "agent"
    if agent_dir.is_dir():
        sockets = sorted(
            agent_dir.glob("s.*.sshd.*"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for s in sockets:
            if s.is_socket():
                return str(s)
    return None


# In-memory task registry
tasks: dict[str, dict[str, Any]] = {}


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
        ssh_sock = _get_ssh_auth_sock()
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

    except Exception as e:
        await stream_to_task(task_id, f"{RED}Error: {e}{RESET}{CRLF}")
        tasks[task_id]["status"] = "failed"


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
    await run_cli_streaming(config, cli_args, task_id)
