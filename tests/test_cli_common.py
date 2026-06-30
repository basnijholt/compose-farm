"""Tests for shared CLI helpers."""

import pytest
import typer

from compose_farm.cli.common import report_results
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
