"""SSH key utilities for compose-farm."""

from __future__ import annotations

import os
from pathlib import Path

# Default key paths for compose-farm SSH key
# Keys are stored in a subdirectory for cleaner docker volume mounting
SSH_KEY_DIR = Path.home() / ".ssh" / "compose-farm"
SSH_KEY_PATH = SSH_KEY_DIR / "id_ed25519"
SSH_PUBKEY_PATH = SSH_KEY_PATH.with_suffix(".pub")


def get_ssh_auth_sock() -> str | None:
    """Get SSH_AUTH_SOCK, auto-detecting forwarded agent if needed.

    Checks in order:
    1. SSH_AUTH_SOCK environment variable (if socket exists)
    2. Forwarded agent sockets in ~/.ssh/agent/ (most recent first)

    Returns the socket path or None if no valid socket found.
    """
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


def get_ssh_env() -> dict[str, str]:
    """Get environment dict for SSH subprocess with auto-detected agent.

    Returns a copy of the current environment with SSH_AUTH_SOCK set
    to the auto-detected agent socket (if found).
    """
    env = os.environ.copy()
    sock = get_ssh_auth_sock()
    if sock:
        env["SSH_AUTH_SOCK"] = sock
    return env


def key_exists() -> bool:
    """Check if the compose-farm SSH key pair exists."""
    return SSH_KEY_PATH.exists() and SSH_PUBKEY_PATH.exists()


def get_key_path() -> Path | None:
    """Get the SSH key path if it exists, None otherwise."""
    return SSH_KEY_PATH if key_exists() else None


def get_pubkey_content() -> str | None:
    """Get the public key content if it exists, None otherwise."""
    if not SSH_PUBKEY_PATH.exists():
        return None
    return SSH_PUBKEY_PATH.read_text().strip()
