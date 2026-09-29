"""Tests for CLI ssh commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from compose_farm.cli.app import app
from compose_farm.cli.ssh import _copy_key_to_host, _format_connectivity_status, _trust_host_key
from compose_farm.executor import CommandResult
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

    def test_failed_status_reports_host_key_error(self) -> None:
        """Do not mislabel host-key failures as authentication failures."""
        result = CommandResult(
            stack="nas",
            exit_code=255,
            success=False,
            stderr="Host key verification failed.",
        )

        status = _format_connectivity_status(result)

        assert "Host key verification failed." in status
        assert "Auth failed" not in status


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
        missing, ok = MagicMock(returncode=255), MagicMock(returncode=0)
        with patch("compose_farm.cli.ssh.subprocess.run", side_effect=[missing, ok, ok]) as run:
            assert _copy_key_to_host("nas", "192.168.1.10", "root", 22) is True

        command = run.call_args_list[1].args[0]
        assert command[0] == "ssh-copy-id"
        assert "StrictHostKeyChecking=ask" in command
        assert "StrictHostKeyChecking=no" not in command
        assert f"UserKnownHostsFile={SSH_KEY_PATH.parent / 'known_hosts'}" in command

    def test_copy_key_skips_hosts_that_already_accept_it(self) -> None:
        """The check logs in with the compose-farm key only, like compose-farm itself."""
        ok = MagicMock(returncode=0)
        with patch("compose_farm.cli.ssh.subprocess.run", return_value=ok) as run:
            assert _copy_key_to_host("nas", "192.168.1.10", "root", 2222) is True

        [check] = [call.args[0] for call in run.call_args_list]
        assert check[0] == "ssh"
        # Ignore ~/.ssh/config IdentityFile entries and agent keys that already work
        assert check[check.index("-F") + 1] == "/dev/null"
        assert check[check.index("-i") + 1] == str(SSH_KEY_PATH)
        for option in ("IdentitiesOnly=yes", "IdentityAgent=none", "BatchMode=yes"):
            assert option in check
        assert check[check.index("-p") + 1] == "2222"
        assert check[-2:] == ["root@192.168.1.10", "true"]

    def test_copy_key_forces_install_and_verifies_it(self) -> None:
        """ssh-copy-id's own check can pass through another key, so force and re-check."""
        missing, ok = MagicMock(returncode=255), MagicMock(returncode=0)
        with patch("compose_farm.cli.ssh.subprocess.run", side_effect=[missing, ok, ok]) as run:
            assert _copy_key_to_host("nas", "192.168.1.10", "root", 22) is True

        commands = [call.args[0] for call in run.call_args_list]
        assert [c[0] for c in commands] == ["ssh", "ssh-copy-id", "ssh"]
        assert "-f" in commands[1]

    def test_copy_key_fails_when_the_key_still_is_not_accepted(self) -> None:
        """A reported success that doesn't let compose-farm log in is a failure."""
        missing, ok = MagicMock(returncode=255), MagicMock(returncode=0)
        with patch("compose_farm.cli.ssh.subprocess.run", side_effect=[missing, ok, missing]):
            assert _copy_key_to_host("nas", "192.168.1.10", "root", 22) is False

    def test_trust_host_key_uses_agent_without_installing_a_key(self, tmp_path: Path) -> None:
        """Agent users can enroll a host key without changing authentication keys."""
        completed = MagicMock(returncode=0)
        known_hosts = tmp_path / "ssh" / "known_hosts"
        with (
            patch("compose_farm.cli.ssh.SSH_KNOWN_HOSTS_PATH", known_hosts),
            patch("compose_farm.cli.ssh.subprocess.run", return_value=completed) as run,
        ):
            assert _trust_host_key("nas", "192.168.1.10", "root", 2222) is True

        command = run.call_args.args[0]
        assert command[0] == "ssh"
        assert "StrictHostKeyChecking=ask" in command
        assert f"UserKnownHostsFile={known_hosts}" in command
        assert command[-2:] == ["root@192.168.1.10", "true"]
        assert command[command.index("-p") + 1] == "2222"
        assert "-i" not in command

    def test_setup_trust_only_does_not_generate_or_copy_a_key(self, tmp_path: Path) -> None:
        """Trust-only setup must preserve agent-only authentication."""
        config_file = tmp_path / "compose-farm.yaml"
        config_file.write_text("""
hosts:
  nas:
    address: 192.168.1.10
    user: root
stacks:
  test: nas
""")

        with (
            patch("compose_farm.cli.ssh._generate_key") as generate,
            patch("compose_farm.cli.ssh._copy_key_to_host") as copy,
            patch("compose_farm.cli.ssh._trust_host_key", return_value=True) as trust,
        ):
            result = runner.invoke(
                app,
                ["ssh", "setup", "--trust-only", f"--config={config_file}"],
            )

        assert result.exit_code == 0
        generate.assert_not_called()
        copy.assert_not_called()
        trust.assert_called_once_with("nas", "192.168.1.10", "root", 22)


class TestSshHelp:
    """Tests for cf ssh help."""

    def test_ssh_help(self) -> None:
        """Show help for ssh command."""
        result = runner.invoke(app, ["ssh", "--help"])

        assert result.exit_code == 0
        assert "setup" in result.output
        assert "status" in result.output
        assert "keygen" in result.output
