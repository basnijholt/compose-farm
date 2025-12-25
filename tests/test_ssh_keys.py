"""Tests for ssh_keys module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from compose_farm.config import Host
from compose_farm.executor import ssh_connect_kwargs
from compose_farm.ssh_keys import (
    SSH_KEY_PATH,
    get_key_path,
    get_pubkey_content,
    get_ssh_auth_sock,
    get_ssh_env,
    key_exists,
)


class TestGetSshAuthSock:
    """Tests for get_ssh_auth_sock function."""

    def test_returns_env_var_when_socket_exists(self) -> None:
        """Return SSH_AUTH_SOCK env var if the socket exists."""
        mock_path = MagicMock()
        mock_path.is_socket.return_value = True

        with (
            patch.dict(os.environ, {"SSH_AUTH_SOCK": "/tmp/agent.sock"}),
            patch("compose_farm.ssh_keys.Path", return_value=mock_path),
        ):
            result = get_ssh_auth_sock()
            assert result == "/tmp/agent.sock"

    def test_returns_none_when_env_var_not_socket(self, tmp_path: Path) -> None:
        """Return None if SSH_AUTH_SOCK points to non-socket."""
        regular_file = tmp_path / "not_a_socket"
        regular_file.touch()
        with (
            patch.dict(os.environ, {"SSH_AUTH_SOCK": str(regular_file)}),
            patch("compose_farm.ssh_keys.Path.home", return_value=tmp_path),
        ):
            # Should fall through to agent dir check, which won't exist
            result = get_ssh_auth_sock()
            assert result is None

    def test_finds_agent_in_ssh_agent_dir(self, tmp_path: Path) -> None:
        """Find agent socket in ~/.ssh/agent/ directory."""
        # Create agent directory structure with a regular file
        agent_dir = tmp_path / ".ssh" / "agent"
        agent_dir.mkdir(parents=True)
        sock_path = agent_dir / "s.12345.sshd.67890"
        sock_path.touch()  # Create as regular file

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("compose_farm.ssh_keys.Path.home", return_value=tmp_path),
            patch.object(Path, "is_socket", return_value=True),
        ):
            os.environ.pop("SSH_AUTH_SOCK", None)
            result = get_ssh_auth_sock()
            assert result == str(sock_path)

    def test_returns_none_when_no_agent_found(self, tmp_path: Path) -> None:
        """Return None when no SSH agent socket is found."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("compose_farm.ssh_keys.Path.home", return_value=tmp_path),
        ):
            os.environ.pop("SSH_AUTH_SOCK", None)
            result = get_ssh_auth_sock()
            assert result is None


class TestGetSshEnv:
    """Tests for get_ssh_env function."""

    def test_returns_env_with_ssh_auth_sock(self) -> None:
        """Return env dict with SSH_AUTH_SOCK set."""
        with patch("compose_farm.ssh_keys.get_ssh_auth_sock", return_value="/tmp/agent.sock"):
            result = get_ssh_env()
            assert result["SSH_AUTH_SOCK"] == "/tmp/agent.sock"
            # Should include other env vars too
            assert "PATH" in result or len(result) > 1

    def test_returns_env_without_ssh_auth_sock_when_none(self, tmp_path: Path) -> None:
        """Return env without SSH_AUTH_SOCK when no agent found."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("compose_farm.ssh_keys.Path.home", return_value=tmp_path),
        ):
            os.environ.pop("SSH_AUTH_SOCK", None)
            result = get_ssh_env()
            # SSH_AUTH_SOCK should not be set if no agent found
            assert result.get("SSH_AUTH_SOCK") is None


class TestKeyExists:
    """Tests for key_exists function."""

    def test_returns_true_when_both_keys_exist(self, tmp_path: Path) -> None:
        """Return True when both private and public keys exist."""
        key_path = tmp_path / "compose-farm"
        pubkey_path = tmp_path / "compose-farm.pub"
        key_path.touch()
        pubkey_path.touch()

        with (
            patch("compose_farm.ssh_keys.SSH_KEY_PATH", key_path),
            patch("compose_farm.ssh_keys.SSH_PUBKEY_PATH", pubkey_path),
        ):
            assert key_exists() is True

    def test_returns_false_when_private_key_missing(self, tmp_path: Path) -> None:
        """Return False when private key doesn't exist."""
        key_path = tmp_path / "compose-farm"
        pubkey_path = tmp_path / "compose-farm.pub"
        pubkey_path.touch()  # Only public key exists

        with (
            patch("compose_farm.ssh_keys.SSH_KEY_PATH", key_path),
            patch("compose_farm.ssh_keys.SSH_PUBKEY_PATH", pubkey_path),
        ):
            assert key_exists() is False

    def test_returns_false_when_public_key_missing(self, tmp_path: Path) -> None:
        """Return False when public key doesn't exist."""
        key_path = tmp_path / "compose-farm"
        pubkey_path = tmp_path / "compose-farm.pub"
        key_path.touch()  # Only private key exists

        with (
            patch("compose_farm.ssh_keys.SSH_KEY_PATH", key_path),
            patch("compose_farm.ssh_keys.SSH_PUBKEY_PATH", pubkey_path),
        ):
            assert key_exists() is False


class TestGetKeyPath:
    """Tests for get_key_path function."""

    def test_returns_path_when_key_exists(self) -> None:
        """Return key path when key exists."""
        with patch("compose_farm.ssh_keys.key_exists", return_value=True):
            result = get_key_path()
            assert result == SSH_KEY_PATH

    def test_returns_none_when_key_missing(self) -> None:
        """Return None when key doesn't exist."""
        with patch("compose_farm.ssh_keys.key_exists", return_value=False):
            result = get_key_path()
            assert result is None


class TestGetPubkeyContent:
    """Tests for get_pubkey_content function."""

    def test_returns_content_when_exists(self, tmp_path: Path) -> None:
        """Return public key content when file exists."""
        pubkey_content = "ssh-ed25519 AAAA... compose-farm"
        pubkey_path = tmp_path / "compose-farm.pub"
        pubkey_path.write_text(pubkey_content + "\n")

        with patch("compose_farm.ssh_keys.SSH_PUBKEY_PATH", pubkey_path):
            result = get_pubkey_content()
            assert result == pubkey_content

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Return None when public key doesn't exist."""
        pubkey_path = tmp_path / "compose-farm.pub"  # Doesn't exist

        with patch("compose_farm.ssh_keys.SSH_PUBKEY_PATH", pubkey_path):
            result = get_pubkey_content()
            assert result is None


class TestSshConnectKwargs:
    """Tests for ssh_connect_kwargs function."""

    def test_basic_kwargs(self) -> None:
        """Return basic connection kwargs."""
        host = Host(address="example.com", port=22, user="testuser")

        with (
            patch("compose_farm.executor.get_ssh_auth_sock", return_value=None),
            patch("compose_farm.executor.get_key_path", return_value=None),
        ):
            result = ssh_connect_kwargs(host)

            assert result["host"] == "example.com"
            assert result["port"] == 22
            assert result["username"] == "testuser"
            assert result["known_hosts"] is None
            assert "agent_path" not in result
            assert "client_keys" not in result

    def test_includes_agent_path_when_available(self) -> None:
        """Include agent_path when SSH agent is available."""
        host = Host(address="example.com")

        with (
            patch("compose_farm.executor.get_ssh_auth_sock", return_value="/tmp/agent.sock"),
            patch("compose_farm.executor.get_key_path", return_value=None),
        ):
            result = ssh_connect_kwargs(host)

            assert result["agent_path"] == "/tmp/agent.sock"

    def test_includes_client_keys_when_key_exists(self, tmp_path: Path) -> None:
        """Include client_keys when compose-farm key exists."""
        host = Host(address="example.com")
        key_path = tmp_path / "compose-farm"

        with (
            patch("compose_farm.executor.get_ssh_auth_sock", return_value=None),
            patch("compose_farm.executor.get_key_path", return_value=key_path),
        ):
            result = ssh_connect_kwargs(host)

            assert result["client_keys"] == [str(key_path)]

    def test_includes_both_agent_and_key(self, tmp_path: Path) -> None:
        """Prioritize client_keys over agent_path when both available."""
        host = Host(address="example.com")
        key_path = tmp_path / "compose-farm"

        with (
            patch("compose_farm.executor.get_ssh_auth_sock", return_value="/tmp/agent.sock"),
            patch("compose_farm.executor.get_key_path", return_value=key_path),
        ):
            result = ssh_connect_kwargs(host)

            # Agent should be ignored in favor of the dedicated key
            assert "agent_path" not in result
            assert result["client_keys"] == [str(key_path)]

    def test_custom_port(self) -> None:
        """Handle custom SSH port."""
        host = Host(address="example.com", port=2222)

        with (
            patch("compose_farm.executor.get_ssh_auth_sock", return_value=None),
            patch("compose_farm.executor.get_key_path", return_value=None),
        ):
            result = ssh_connect_kwargs(host)

            assert result["port"] == 2222
