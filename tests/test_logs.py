"""Tests for snapshot logging."""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult
from compose_farm.logs import (
    _BATCH_SEPARATOR,
    SnapshotEntry,
    _parse_images_output,
    collect_all_stacks_entries,
    collect_stacks_entries_on_host,
    isoformat,
    load_existing_entries,
    merge_entries,
    write_toml,
)


def test_parse_images_output_handles_list_and_lines() -> None:
    data = [
        {"Service": "svc", "Image": "redis", "Digest": "sha256:abc"},
        {"Service": "svc", "Image": "db", "Digest": "sha256:def"},
    ]
    as_array = _parse_images_output(json.dumps(data))
    assert len(as_array) == 2

    as_lines = _parse_images_output("\n".join(json.dumps(item) for item in data))
    assert len(as_lines) == 2


@pytest.mark.asyncio
async def test_snapshot_preserves_first_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    stack_dir = compose_dir / "svc"
    stack_dir.mkdir()
    (stack_dir / "docker-compose.yml").write_text("services: {}\n")

    config = Config(
        compose_dir=compose_dir,
        hosts={"local": Host(address="localhost")},
        stacks={"svc": "local"},
    )

    sample_json = json.dumps([{"Image": "redis", "Digest": "sha256:abc"}])

    async def mock_run_command(
        host: Host, command: str, stack: str, *, stream: bool, prefix: str
    ) -> CommandResult:
        output = f"{_BATCH_SEPARATOR}svc\n{sample_json}"
        return CommandResult(stack=stack, exit_code=0, success=True, stdout=output)

    monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

    log_path = tmp_path / "dockerfarm-log.toml"

    # First snapshot
    first_time = datetime(2025, 1, 1, tzinfo=UTC)
    first_entries = await collect_stacks_entries_on_host(config, "local", ["svc"], now=first_time)
    first_iso = isoformat(first_time)
    merged = merge_entries([], first_entries, now_iso=first_iso)
    meta = {"generated_at": first_iso, "compose_dir": str(config.compose_dir)}
    write_toml(log_path, meta=meta, entries=merged)

    after_first = tomllib.loads(log_path.read_text())
    first_seen = after_first["entries"][0]["first_seen"]

    # Second snapshot
    second_time = datetime(2025, 2, 1, tzinfo=UTC)
    second_entries = await collect_stacks_entries_on_host(config, "local", ["svc"], now=second_time)
    second_iso = isoformat(second_time)
    existing = load_existing_entries(log_path)
    merged = merge_entries(existing, second_entries, now_iso=second_iso)
    meta = {"generated_at": second_iso, "compose_dir": str(config.compose_dir)}
    write_toml(log_path, meta=meta, entries=merged)

    after_second = tomllib.loads(log_path.read_text())
    entry = after_second["entries"][0]
    assert entry["first_seen"] == first_seen
    assert entry["last_seen"].startswith("2025-02-01")


class TestBatchCollectStackEntries:
    """Tests for batch stack image collection (1 SSH call per host)."""

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
    async def test_collect_stacks_entries_on_host_batches_commands(
        self, config_with_stacks: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that multiple stacks are collected in a single SSH call."""
        call_count = {"count": 0}

        async def mock_run_command(
            host: Host, command: str, stack: str, *, stream: bool, prefix: str
        ) -> CommandResult:
            call_count["count"] += 1
            # Simulate batched output
            output_lines = []
            for s in ["plex", "jellyfin"]:
                output_lines.append(f"{_BATCH_SEPARATOR}{s}")
                output_lines.append(
                    json.dumps([{"Image": f"{s}-image", "Digest": f"sha256:{s}hash"}])
                )
            return CommandResult(
                stack=stack, exit_code=0, success=True, stdout="\n".join(output_lines)
            )

        monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(
            config_with_stacks, "host1", ["plex", "jellyfin"], now=now
        )

        # Should only make 1 call (not 2)
        assert call_count["count"] == 1
        # Should have entries for both stacks
        stack_names = {e.stack for e in entries}
        assert stack_names == {"plex", "jellyfin"}

    @pytest.mark.asyncio
    async def test_collect_all_stacks_entries_groups_by_host(
        self, config_with_stacks: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that stacks are grouped by host."""
        calls_per_host: dict[str, int] = {}

        async def mock_collect_on_host(
            config: Config, host_name: str, stacks: list[str], *, now: datetime
        ) -> list[SnapshotEntry]:
            calls_per_host[host_name] = calls_per_host.get(host_name, 0) + 1
            return []

        monkeypatch.setattr(
            "compose_farm.logs.collect_stacks_entries_on_host", mock_collect_on_host
        )

        now = datetime(2025, 1, 1, tzinfo=UTC)
        await collect_all_stacks_entries(
            config_with_stacks,
            {"host1": ["plex", "jellyfin"], "host2": ["sonarr"]},
            now=now,
        )

        # Should call once per host
        assert calls_per_host == {"host1": 1, "host2": 1}

    @pytest.mark.asyncio
    async def test_collect_stacks_entries_parses_batched_output(
        self, config_with_stacks: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that batched output is correctly parsed into entries."""

        async def mock_run_command(
            host: Host, command: str, stack: str, *, stream: bool, prefix: str
        ) -> CommandResult:
            # Simulate multi-line JSON output per stack
            plex_json = json.dumps([{"Image": "plex-server:latest", "Digest": "sha256:aaa"}])
            jellyfin_json = json.dumps(
                [
                    {"Image": "jellyfin:10.8", "Digest": "sha256:bbb"},
                    {"Image": "redis:7", "Digest": "sha256:ccc"},
                ]
            )
            output = (
                f"{_BATCH_SEPARATOR}plex\n{plex_json}\n{_BATCH_SEPARATOR}jellyfin\n{jellyfin_json}"
            )
            return CommandResult(stack=stack, exit_code=0, success=True, stdout=output)

        monkeypatch.setattr("compose_farm.logs.run_command", mock_run_command)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(
            config_with_stacks, "host1", ["plex", "jellyfin"], now=now
        )

        # Verify we got all 3 entries
        assert len(entries) == 3
        # Verify they're associated with correct stacks
        plex_entries = [e for e in entries if e.stack == "plex"]
        jellyfin_entries = [e for e in entries if e.stack == "jellyfin"]
        assert len(plex_entries) == 1
        assert len(jellyfin_entries) == 2
        assert plex_entries[0].image == "plex-server:latest"

    @pytest.mark.asyncio
    async def test_collect_stacks_entries_empty_list(self, config_with_stacks: Config) -> None:
        """Empty stack list returns empty entries."""
        now = datetime(2025, 1, 1, tzinfo=UTC)
        entries = await collect_stacks_entries_on_host(config_with_stacks, "host1", [], now=now)
        assert entries == []
