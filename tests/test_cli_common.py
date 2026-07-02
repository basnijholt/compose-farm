"""Tests for shared CLI helpers."""

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from compose_farm.cli.common import maybe_regenerate_traefik, report_results
from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult


def test_report_results_includes_host_for_failed_stack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = [
        CommandResult(stack="clip-files-ui", exit_code=1, success=False, host="hp"),
        CommandResult(stack="uptime", exit_code=0, success=True, host="nuc"),
    ]

    with pytest.raises(typer.Exit):
        report_results(results)

    captured = capsys.readouterr()
    assert "clip-files-ui failed with exit code 1 on hp" in captured.err
    assert "1/2 stacks succeeded" in captured.out


def test_report_results_keeps_old_format_without_host(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit):
        report_results([CommandResult(stack="clip-files-ui", exit_code=1, success=False)])

    captured = capsys.readouterr()
    assert "clip-files-ui failed with exit code 1" in captured.err
    assert "on " not in captured.err


def test_report_results_uses_display_label_without_host(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit):
        report_results(
            [CommandResult(stack="multi", exit_code=1, success=False, label="multi@host1")]
        )

    captured = capsys.readouterr()
    assert "multi@host1 failed with exit code 1" in captured.err


def test_maybe_regenerate_traefik_warns_on_permission_error(tmp_path: Path) -> None:
    cfg = Config(
        compose_dir=tmp_path / "compose",
        hosts={"host1": Host(address="localhost")},
        stacks={"traefik": "host1"},
        traefik_file=tmp_path / "readonly" / "compose-farm.yml",
    )

    with (
        patch("compose_farm.traefik.generate_traefik_config", return_value=({}, [])),
        patch("compose_farm.traefik.render_traefik_config", return_value="http: {}\n"),
        patch("pathlib.Path.mkdir", side_effect=PermissionError("denied")),
        patch("compose_farm.cli.common.print_warning") as mock_warning,
    ):
        maybe_regenerate_traefik(cfg)

    mock_warning.assert_called_once_with("Failed to update traefik config: denied")


def test_maybe_regenerate_traefik_propagates_input_permission_error(
    tmp_path: Path,
) -> None:
    cfg = Config(
        compose_dir=tmp_path / "compose",
        hosts={"host1": Host(address="localhost")},
        stacks={"traefik": "host1"},
        traefik_file=tmp_path / "compose-farm.yml",
    )

    with (
        patch(
            "compose_farm.traefik.generate_traefik_config",
            side_effect=PermissionError("compose denied"),
        ),
        patch("compose_farm.cli.common.print_warning") as mock_warning,
        pytest.raises(PermissionError, match="compose denied"),
    ):
        maybe_regenerate_traefik(cfg)

    mock_warning.assert_not_called()
