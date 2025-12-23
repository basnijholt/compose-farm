"""Tests for snapshot logging."""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult
from compose_farm.logs import (
    _SECTION_SEPARATOR,
    collect_stacks_entries_on_host,
    isoformat,
    load_existing_entries,
    merge_entries,
    write_toml,
)


def _make_mock_output(
    project_images: dict[str, list[str]], image_info: list[dict[str, object]]
) -> str:
    """Build mock output matching the 2-docker-command format."""
    # Section 1: project|image pairs from docker ps
    ps_lines = [
        f"{project}|{image}" for project, images in project_images.items() for image in images
    ]

    # Section 2: JSON array from docker image inspect
    image_json = json.dumps(image_info)

    return f"{chr(10).join(ps_lines)}\n{_SECTION_SEPARATOR}\n{image_json}"


class TestCollectStacksEntriesOnHost:
    """Tests for collect_stacks_entries_on_host (2 docker commands per host)."""

    @pytest.fixture
    def config_with_stacks(self, tmp_path: Path) -> Config:
        """Create a config with multiple stacks."""
        compose_dir = tmp_path / "compose"
        compose_dir.mkdir()
        for stack in ["plex", "jellyfin", "sonarr"]:
            stack_dir = compose_dir / stack
            stack_dir.mkdir()
            (stack_dir / "docker-compose.yml").write_text("services: {}\n")

        return Config(
            compose_dir=compose_dir,
            hosts={"host1": Host(address="localhost"), "host2": Host(address="localhost")},
            stacks={"plex": "host1", "jellyfin": "host1", "sonarr": "host2"},
        )

    @pytest.mark.asyncio
    async def test_single_ssh_call(
        self, config_with_stacks: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify only 1 SSH call is made regardless of stack count."""
        call_count = {"count": 0}

        async def mock_run_command(
            host: Host, command: str, stack: str, *, stream: bool, prefix: str
        ) -> CommandResult:
            call_count["count"] += 1
            output = _make_mock_output(
                {"plex": ["plex:latest"], "jellyfin": ["jellyfin:latest"]},
                [
                    {
                        "RepoTags": ["plex:latest"],
                        "Id": "sha256:aaa",
                        "RepoDigests": ["plex@sha256:aaa"],
                    },
                    {
                        "RepoTags": ["jellyfin:latest"],
                        "Id": "sha256:bbb",
                        "RepoDigests": ["jellyfin@sha256:bbb"],
                    },
                ],
            )
            return CommandResult(stack=stack, exit_code=0, success=True, stdout=output)

        monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(
            config_with_stacks, "host1", {"plex", "jellyfin"}, now=now
        )

        assert call_count["count"] == 1
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_filters_to_requested_stacks(
        self, config_with_stacks: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only return entries for stacks we asked for, even if others are running."""

        async def mock_run_command(
            host: Host, command: str, stack: str, *, stream: bool, prefix: str
        ) -> CommandResult:
            # Docker ps shows 3 stacks, but we only want plex
            output = _make_mock_output(
                {
                    "plex": ["plex:latest"],
                    "jellyfin": ["jellyfin:latest"],
                    "other": ["other:latest"],
                },
                [
                    {
                        "RepoTags": ["plex:latest"],
                        "Id": "sha256:aaa",
                        "RepoDigests": ["plex@sha256:aaa"],
                    },
                    {
                        "RepoTags": ["jellyfin:latest"],
                        "Id": "sha256:bbb",
                        "RepoDigests": ["j@sha256:bbb"],
                    },
                    {
                        "RepoTags": ["other:latest"],
                        "Id": "sha256:ccc",
                        "RepoDigests": ["o@sha256:ccc"],
                    },
                ],
            )
            return CommandResult(stack=stack, exit_code=0, success=True, stdout=output)

        monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(
            config_with_stacks, "host1", {"plex"}, now=now
        )

        assert len(entries) == 1
        assert entries[0].stack == "plex"

    @pytest.mark.asyncio
    async def test_multiple_images_per_stack(
        self, config_with_stacks: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stack with multiple containers/images returns multiple entries."""

        async def mock_run_command(
            host: Host, command: str, stack: str, *, stream: bool, prefix: str
        ) -> CommandResult:
            output = _make_mock_output(
                {"plex": ["plex:latest", "redis:7"]},
                [
                    {
                        "RepoTags": ["plex:latest"],
                        "Id": "sha256:aaa",
                        "RepoDigests": ["p@sha256:aaa"],
                    },
                    {"RepoTags": ["redis:7"], "Id": "sha256:bbb", "RepoDigests": ["r@sha256:bbb"]},
                ],
            )
            return CommandResult(stack=stack, exit_code=0, success=True, stdout=output)

        monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(
            config_with_stacks, "host1", {"plex"}, now=now
        )

        assert len(entries) == 2
        images = {e.image for e in entries}
        assert images == {"plex:latest", "redis:7"}

    @pytest.mark.asyncio
    async def test_empty_stacks_returns_empty(self, config_with_stacks: Config) -> None:
        """Empty stack set returns empty entries without making SSH call."""
        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(config_with_stacks, "host1", set(), now=now)
        assert entries == []

    @pytest.mark.asyncio
    async def test_ssh_failure_returns_empty(
        self, config_with_stacks: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SSH failure returns empty list instead of raising."""

        async def mock_run_command(
            host: Host, command: str, stack: str, *, stream: bool, prefix: str
        ) -> CommandResult:
            return CommandResult(stack=stack, exit_code=1, success=False, stdout="", stderr="error")

        monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(
            config_with_stacks, "host1", {"plex"}, now=now
        )

        assert entries == []


class TestSnapshotMerging:
    """Tests for merge_entries preserving first_seen."""

    @pytest.fixture
    def config(self, tmp_path: Path) -> Config:
        compose_dir = tmp_path / "compose"
        compose_dir.mkdir()
        stack_dir = compose_dir / "svc"
        stack_dir.mkdir()
        (stack_dir / "docker-compose.yml").write_text("services: {}\n")

        return Config(
            compose_dir=compose_dir,
            hosts={"local": Host(address="localhost")},
            stacks={"svc": "local"},
        )

    @pytest.mark.asyncio
    async def test_preserves_first_seen(
        self, tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repeated snapshots preserve first_seen timestamp."""

        async def mock_run_command(
            host: Host, command: str, stack: str, *, stream: bool, prefix: str
        ) -> CommandResult:
            output = _make_mock_output(
                {"svc": ["redis:latest"]},
                [
                    {
                        "RepoTags": ["redis:latest"],
                        "Id": "sha256:abc",
                        "RepoDigests": ["r@sha256:abc"],
                    }
                ],
            )
            return CommandResult(stack=stack, exit_code=0, success=True, stdout=output)

        monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

        log_path = tmp_path / "dockerfarm-log.toml"

        # First snapshot
        first_time = datetime(2025, 1, 1, tzinfo=UTC)
        first_entries = await collect_stacks_entries_on_host(
            config, "local", {"svc"}, now=first_time
        )
        first_iso = isoformat(first_time)
        merged = merge_entries([], first_entries, now_iso=first_iso)
        meta = {"generated_at": first_iso, "compose_dir": str(config.compose_dir)}
        write_toml(log_path, meta=meta, entries=merged)

        after_first = tomllib.loads(log_path.read_text())
        first_seen = after_first["entries"][0]["first_seen"]

        # Second snapshot
        second_time = datetime(2025, 2, 1, tzinfo=UTC)
        second_entries = await collect_stacks_entries_on_host(
            config, "local", {"svc"}, now=second_time
        )
        second_iso = isoformat(second_time)
        existing = load_existing_entries(log_path)
        merged = merge_entries(existing, second_entries, now_iso=second_iso)
        meta = {"generated_at": second_iso, "compose_dir": str(config.compose_dir)}
        write_toml(log_path, meta=meta, entries=merged)

        after_second = tomllib.loads(log_path.read_text())
        entry = after_second["entries"][0]
        assert entry["first_seen"] == first_seen
        assert entry["last_seen"].startswith("2025-02-01")
