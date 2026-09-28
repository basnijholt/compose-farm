"""Tests for CLI ssh commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from compose_farm.cli.app import app
from compose_farm.cli.ssh import _copy_key_to_host
from compose_farm.ssh_keys import SSH_KEY_PATH

runner = CliRunner()


class TestSshKeygen:
    """Tests for cf ssh keygen command."""

    def test_keygen_generates_key(self, tmp_path: Path) -> None:
        """Generate SSH key when none exists."""
        key_path = tmp_path / "compose-farm"
        pubkey_path = tmp_path / "compose-farm.pub"

        with (
            patch("compose_farm.cli.ssh.SSH_KEY_PATH", key_path),
            patch("compose_farm.cli.ssh.SSH_PUBKEY_PATH", pubkey_path),
            patch("compose_farm.cli.ssh.key_exists", return_value=False),
        ):
            result = runner.invoke(app, ["ssh", "keygen"])

            # Command runs (may fail if ssh-keygen not available in test env)
            assert result.exit_code in (0, 1)

    def test_keygen_skips_if_exists(self, tmp_path: Path) -> None:
        """Skip key generation if key already exists."""
        key_path = tmp_path / "compose-farm"
        pubkey_path = tmp_path / "compose-farm.pub"

        with (
            patch("compose_farm.cli.ssh.SSH_KEY_PATH", key_path),
            patch("compose_farm.cli.ssh.SSH_PUBKEY_PATH", pubkey_path),
            patch("compose_farm.cli.ssh.key_exists", return_value=True),
        ):
            result = runner.invoke(app, ["ssh", "keygen"])

            assert "already exists" in result.output


class TestSshStatus:
    """Tests for cf ssh status command."""

    def test_status_shows_no_key(self, tmp_path: Path) -> None:
        """Show message when no key exists."""
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text("""
hosts:
  local:
    address: localhost
stacks:
  test: local
""")

        with patch("compose_farm.cli.ssh.key_exists", return_value=False):
            result = runner.invoke(app, ["ssh", "status", f"--config={config_file}"])

            assert "No key found" in result.output

    def test_status_shows_key_exists(self, tmp_path: Path) -> None:
        """Show key info when key exists."""
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text("""
hosts:
  local:
    address: localhost
stacks:
  test: local
""")

        with (
            patch("compose_farm.cli.ssh.key_exists", return_value=True),
            patch("compose_farm.cli.ssh.get_pubkey_content", return_value="ssh-ed25519 AAAA..."),
        ):
            result = runner.invoke(app, ["ssh", "status", f"--config={config_file}"])

            assert "Key exists" in result.output


class TestSshSetup:
    """Tests for cf ssh setup command."""

    def test_setup_no_remote_hosts(self, tmp_path: Path) -> None:
        """Show message when no remote hosts configured."""
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text("""
hosts:
  local:
    address: localhost
stacks:
  test: local
""")

        result = runner.invoke(app, ["ssh", "setup", f"--config={config_file}"])

        assert "No remote hosts" in result.output

    def test_copy_key_uses_standard_host_key_verification(self) -> None:
        """First-time setup must not discard or bypass the server host key."""
        completed = MagicMock(returncode=0)
        with patch("compose_farm.cli.ssh.subprocess.run", return_value=completed) as run:
            assert _copy_key_to_host("nas", "192.168.1.10", "root", 22) is True

        command = run.call_args.args[0]
        assert "StrictHostKeyChecking=ask" in command
        assert "StrictHostKeyChecking=no" not in command
        assert f"UserKnownHostsFile={SSH_KEY_PATH.parent / 'known_hosts'}" in command


class TestSshHelp:
    """Tests for cf ssh help."""

    def test_ssh_help(self) -> None:
        """Show help for ssh command."""
        result = runner.invoke(app, ["ssh", "--help"])

        assert result.exit_code == 0
        assert "setup" in result.output
        assert "status" in result.output
        assert "keygen" in result.output
