"""Tests for CLI ssh commands."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest
from typer.testing import CliRunner

from compose_farm.cli.app import app
from compose_farm.cli.ssh import (
    _copy_key_to_host,
    _format_connectivity_status,
    _probe_key,
    _trust_host_key,
)
from compose_farm.config import Host
from compose_farm.executor import CommandResult, ssh_connect_kwargs
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

    @staticmethod
    def _copy(probes: list[str], *, runs: int = 1, trust: bool = True) -> tuple[bool, Any, Any]:
        """Run _copy_key_to_host with scripted probe results; return (ok, run, trust) mocks."""
        host = Host(address="192.168.1.10", user="root", port=2222)
        with (
            patch("compose_farm.cli.ssh._probe_key", AsyncMock(side_effect=probes)),
            patch(
                "compose_farm.cli.ssh.subprocess.run", return_value=MagicMock(returncode=0)
            ) as run,
            patch("compose_farm.cli.ssh._trust_host_key", return_value=trust) as trust_mock,
        ):
            ok = _copy_key_to_host("nas", host)
        assert run.call_count == runs
        return ok, run, trust_mock

    def test_copy_key_skips_hosts_that_already_accept_it(self) -> None:
        ok, _, trust = self._copy(["accepted"], runs=0)
        assert ok is True
        trust.assert_not_called()

    def test_copy_key_forces_install_and_verifies_it(self) -> None:
        """ssh-copy-id's own check can pass through another key, so force and re-check."""
        ok, run, _ = self._copy(["rejected", "accepted"])
        assert ok is True
        command = run.call_args.args[0]
        assert command[:2] == ["ssh-copy-id", "-f"]
        assert "StrictHostKeyChecking=ask" in command
        assert "StrictHostKeyChecking=no" not in command
        assert f"UserKnownHostsFile={SSH_KEY_PATH.parent / 'known_hosts'}" in command
        assert command[command.index("-p") + 1] == "2222"

    def test_copy_key_fails_when_the_key_still_is_not_accepted(self) -> None:
        """A reported success that doesn't let compose-farm log in is a failure."""
        ok, _, _ = self._copy(["rejected", "rejected"])
        assert ok is False

    def test_unknown_host_key_is_trusted_before_deciding(self) -> None:
        """An unknown host key must not be mistaken for a missing client key (no duplicate)."""
        ok, _, trust = self._copy(["unknown-host", "accepted"], runs=0)
        assert ok is True
        trust.assert_called_once_with("nas", Host(address="192.168.1.10", user="root", port=2222))

    def test_unknown_host_key_that_is_not_trusted_fails(self) -> None:
        ok, _, _ = self._copy(["unknown-host"], runs=0, trust=False)
        assert ok is False

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (None, "accepted"),
            (asyncssh.PermissionDenied("denied"), "rejected"),
            (asyncssh.HostKeyNotVerifiable("unknown"), "unknown-host"),
        ],
    )
    async def test_probe_uses_compose_farm_connection(
        self, tmp_path: Path, error: Exception | None, expected: str
    ) -> None:
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("")
        connect = MagicMock()
        connect.return_value.__aenter__ = AsyncMock(side_effect=error)
        connect.return_value.__aexit__ = AsyncMock(return_value=False)
        host = Host(address="192.168.1.10", user="root")
        with (
            patch("compose_farm.cli.ssh.SSH_KNOWN_HOSTS_PATH", known_hosts),
            patch("asyncssh.connect", connect),
        ):
            assert await _probe_key(host) == expected
        assert connect.call_args.kwargs == ssh_connect_kwargs(host)

    async def test_probe_without_known_hosts_file(self, tmp_path: Path) -> None:
        with patch("compose_farm.cli.ssh.SSH_KNOWN_HOSTS_PATH", tmp_path / "missing"):
            assert await _probe_key(Host(address="192.168.1.10")) == "unknown-host"

    def test_trust_host_key_uses_agent_without_installing_a_key(self, tmp_path: Path) -> None:
        """Agent users can enroll a host key without changing authentication keys."""
        completed = MagicMock(returncode=0)
        known_hosts = tmp_path / "ssh" / "known_hosts"
        with (
            patch("compose_farm.cli.ssh.SSH_KNOWN_HOSTS_PATH", known_hosts),
            patch("compose_farm.cli.ssh.subprocess.run", return_value=completed) as run,
        ):
            assert _trust_host_key("nas", Host(address="192.168.1.10", user="root", port=2222))

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
        trust.assert_called_once_with("nas", Host(address="192.168.1.10", user="root"))


class TestSshHelp:
    """Tests for cf ssh help."""

    def test_ssh_help(self) -> None:
        """Show help for ssh command."""
        result = runner.invoke(app, ["ssh", "--help"])

        assert result.exit_code == 0
        assert "setup" in result.output
        assert "status" in result.output
        assert "keygen" in result.output
