"""SSH key utilities for compose-farm."""

from __future__ import annotations

from pathlib import Path

# Default key paths for compose-farm SSH key
SSH_KEY_PATH = Path.home() / ".ssh" / "compose-farm"
SSH_PUBKEY_PATH = SSH_KEY_PATH.with_suffix(".pub")


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
