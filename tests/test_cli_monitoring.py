"""Tests for CLI monitoring commands (stats)."""

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from compose_farm.cli.monitoring import _build_summary_table, stats
from compose_farm.config import Config, Host
from compose_farm.glances import ContainerStats


def _make_config(tmp_path: Path, glances_stack: str | None = None) -> Config:
    """Create a minimal config for testing."""
    config_path = tmp_path / "compose-farm.yaml"
    config_path.write_text("")

    return Config(
        compose_dir=tmp_path / "compose",
        hosts={"host1": Host(address="localhost")},
        stacks={"svc1": "host1"},
        config_path=config_path,
        glances_stack=glances_stack,
    )


class TestStatsCommand:
    """Tests for the stats command."""

    def test_stats_containers_requires_glances_config(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--containers fails if glances_stack is not configured."""
        cfg = _make_config(tmp_path, glances_stack=None)

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            pytest.raises(typer.Exit) as exc_info,
        ):
            stats(live=False, containers=True, host=None, config=None)

        assert exc_info.value.exit_code == 1
        captured = capsys.readouterr()
        assert "Glances not configured" in captured.err

    def test_stats_containers_success(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--containers fetches and displays container stats."""
        cfg = _make_config(tmp_path, glances_stack="glances")

        mock_containers = [
            ContainerStats(
                name="nginx",
                host="host1",
                status="running",
                image="nginx:latest",
                cpu_percent=10.5,
                memory_usage=100 * 1024 * 1024,
                memory_limit=1024 * 1024 * 1024,
                memory_percent=10.0,
                network_rx=1000,
                network_tx=2000,
                uptime="1h",
                ports="80->80",
                engine="docker",
                stack="web",
                service="nginx",
            )
        ]

        async def mock_fetch_async(
            cfg: Config, hosts: list[str] | None = None
        ) -> list[ContainerStats]:
            return mock_containers

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.glances.fetch_all_container_stats", side_effect=mock_fetch_async
            ) as mock_fetch,
        ):
            stats(live=False, containers=True, host=None, config=None)

            mock_fetch.assert_called_once_with(cfg, hosts=None)

        captured = capsys.readouterr()
        # Verify table output
        assert "nginx" in captured.out
        assert "host1" in captured.out
        assert "runni" in captured.out
        assert "10.5%" in captured.out

    def test_stats_containers_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--containers handles empty result gracefully."""
        cfg = _make_config(tmp_path, glances_stack="glances")

        async def mock_fetch_empty(
            cfg: Config, hosts: list[str] | None = None
        ) -> list[ContainerStats]:
            return []

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            patch("compose_farm.glances.fetch_all_container_stats", side_effect=mock_fetch_empty),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                stats(live=False, containers=True, host=None, config=None)

            assert exc_info.value.exit_code == 0

        captured = capsys.readouterr()
        assert "No containers found" in captured.err

    def test_stats_containers_host_filter(self, tmp_path: Path) -> None:
        """--host limits container queries in --containers mode."""
        cfg = _make_config(tmp_path, glances_stack="glances")

        async def mock_fetch_async(
            cfg: Config, hosts: list[str] | None = None
        ) -> list[ContainerStats]:
            return []

        with (
            patch("compose_farm.cli.monitoring.load_config_or_exit", return_value=cfg),
            patch(
                "compose_farm.glances.fetch_all_container_stats", side_effect=mock_fetch_async
            ) as mock_fetch,
            pytest.raises(typer.Exit),
        ):
            stats(live=False, containers=True, host="host1", config=None)

        mock_fetch.assert_called_once_with(cfg, hosts=["host1"])

    def test_stats_summary_respects_host_filter(self, tmp_path: Path) -> None:
        """--host filters summary counts to the selected host."""
        compose_dir = tmp_path / "compose"
        for name in ("svc1", "svc2", "svc3"):
            stack_dir = compose_dir / name
            stack_dir.mkdir(parents=True)
            (stack_dir / "compose.yaml").write_text("services: {}\n")

        config_path = tmp_path / "compose-farm.yaml"
        config_path.write_text("")

        cfg = Config(
            compose_dir=compose_dir,
            hosts={
                "host1": Host(address="localhost"),
                "host2": Host(address="127.0.0.2"),
            },
            stacks={"svc1": "host1", "svc2": "host2", "svc3": "host1"},
            config_path=config_path,
        )

        state: dict[str, str | list[str]] = {"svc1": "host1", "svc2": "host2"}
        table = _build_summary_table(cfg, state, pending=[], host_filter="host1")
        labels = table.columns[0]._cells
        values = table.columns[1]._cells
        summary = dict(zip(labels, values, strict=True))

        assert summary["Total hosts"] == "1"
        assert summary["Stacks (configured)"] == "2"
        assert summary["Stacks (tracked)"] == "1"
        assert summary["Compose files on disk"] == "2"
