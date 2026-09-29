"""Tests for cf plugins install."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from click.testing import Result
from typer.testing import CliRunner

from compose_farm.cli.app import app

runner = CliRunner()


def _config(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "compose-farm.yaml"
    path.write_text(
        "compose_dir: /opt/compose\nhosts: {h1: localhost}\nstacks: {web: h1}\n" + extra
    )
    return path


def _install(path: Path, *, uv: str | None, returncode: int = 0) -> tuple[Result, list[list[str]]]:
    """Run `cf plugins install` with the installer faked; return the result and its commands."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        assert check is False
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode)

    with (
        patch("compose_farm.cli.plugins.shutil.which", return_value=uv),
        patch("compose_farm.cli.plugins.subprocess.run", side_effect=fake_run),
    ):
        result = runner.invoke(app, ["plugins", "install", "-c", str(path)])
    return result, calls


def test_installs_with_uv_into_this_python(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: [pkg-a, ./b]\nplugins: {commands: null}\n")
    result, calls = _install(path, uv="/usr/bin/uv")
    assert result.exit_code == 0, result.output
    assert calls == [["/usr/bin/uv", "pip", "install", "--python", sys.executable, "pkg-a", "./b"]]
    assert "Plugins: commands" in result.output


def test_falls_back_to_pip(tmp_path: Path) -> None:
    result, calls = _install(_config(tmp_path, "plugin_packages: [pkg-a]\n"), uv=None)
    assert result.exit_code == 0, result.output
    assert calls == [[sys.executable, "-m", "pip", "install", "pkg-a"]]
    assert "Plugins: none enabled" in result.output


def test_echoes_extras_verbatim(tmp_path: Path) -> None:
    result, _ = _install(_config(tmp_path, "plugin_packages: ['pkg[extra]']\n"), uv=None)
    assert "'pkg[extra]'" in result.output


def test_reads_config_whose_plugins_are_missing(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: [pkg-a]\nplugins: {nope: null}\n")
    result, calls = _install(path, uv="/usr/bin/uv")
    assert len(calls) == 1
    assert result.exit_code == 1
    assert "Unknown plugin(s): nope" in result.output


def test_failing_installer_exits_nonzero(tmp_path: Path) -> None:
    result, _ = _install(
        _config(tmp_path, "plugin_packages: [pkg-a]\n"), uv="/usr/bin/uv", returncode=2
    )
    assert result.exit_code == 1
    assert "Installing plugin_packages failed" in result.output


def test_no_packages_warns(tmp_path: Path) -> None:
    result, calls = _install(_config(tmp_path, ""), uv="/usr/bin/uv")
    assert result.exit_code == 0
    assert calls == []
    assert "No plugin_packages" in result.output
