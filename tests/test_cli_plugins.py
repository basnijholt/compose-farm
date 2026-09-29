"""Tests for cf plugins install."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import Result
from typer.testing import CliRunner

from compose_farm.cli.app import app
from compose_farm.cli.plugins import _requirement

runner = CliRunner()


def _config(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "compose-farm.yaml"
    path.write_text(
        "compose_dir: /opt/compose\nhosts: {h1: localhost}\nstacks: {web: h1}\n" + extra
    )
    return path


def _install(
    path: Path, *, uv: str | None, pip: bool = True, returncode: int = 0
) -> tuple[Result, list[list[str]]]:
    """Run `cf plugins install` with the installer faked; return the result and its commands."""
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], *, check: bool, cwd: Path
    ) -> subprocess.CompletedProcess[bytes]:
        assert check is False
        assert cwd == path.parent  # Relative local paths resolve next to the config
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode)

    with (
        patch("compose_farm.cli.plugins.shutil.which", return_value=uv),
        patch("compose_farm.cli.plugins.find_spec", return_value=object() if pip else None),
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
    assert "Plugins: none enabled" in result.output


def test_no_packages_still_checks_plugins(tmp_path: Path) -> None:
    result, calls = _install(_config(tmp_path, "plugins: {nope: null}\n"), uv="/usr/bin/uv")
    assert result.exit_code == 1
    assert calls == []
    assert "Unknown plugin(s): nope" in result.output


def test_needs_uv_or_pip(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: [pkg-a]\n")
    result, calls = _install(path, uv=None, pip=False)
    assert result.exit_code == 1
    assert calls == []
    assert "needs uv on PATH or pip" in result.output


@pytest.mark.parametrize(
    ("package", "requirement"),
    [
        ("github:o/r", "git+https://github.com/o/r"),
        ("github:o/r@v1", "git+https://github.com/o/r@v1"),
        ("github:o/r/sub/dir", "git+https://github.com/o/r#subdirectory=sub/dir"),
        ("github:o/r/sub@feat/x", "git+https://github.com/o/r@feat/x#subdirectory=sub"),
        ("pkg>=1", "pkg>=1"),
        ("git+https://example.com/r", "git+https://example.com/r"),
    ],
)
def test_github_shorthand(package: str, requirement: str) -> None:
    assert _requirement(package) == requirement


def test_github_shorthand_needs_owner_and_repo(tmp_path: Path) -> None:
    result, calls = _install(_config(tmp_path, "plugin_packages: ['github:o']\n"), uv=None)
    assert result.exit_code == 1
    assert calls == []
    assert "use github:OWNER/REPO/SUBDIR@REF" in result.output


def test_installs_expanded_shorthand(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: ['github:o/r/p']\n")
    _, calls = _install(path, uv=None)
    assert calls == [
        [sys.executable, "-m", "pip", "install", "git+https://github.com/o/r#subdirectory=p"]
    ]
