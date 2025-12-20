"""Tests for snapshot logging."""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult
from compose_farm.logs import (
    _parse_images_output,
    collect_stack_entries,
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
async def test_snapshot_preserves_first_seen(tmp_path: Path) -> None:
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

    sample_output = json.dumps([{"Service": "svc", "Image": "redis", "Digest": "sha256:abc"}])

    async def fake_run_compose(
        _cfg: Config, stack: str, compose_cmd: str, *, stream: bool = True
    ) -> CommandResult:
        assert compose_cmd == "images --format json"
        assert stream is False or stream is True
        return CommandResult(
            stack=stack,
            exit_code=0,
            success=True,
            stdout=sample_output,
            stderr="",
        )

    log_path = tmp_path / "dockerfarm-log.toml"

    # First snapshot
    first_time = datetime(2025, 1, 1, tzinfo=UTC)
    first_entries = await collect_stack_entries(
        config, "svc", now=first_time, run_compose_fn=fake_run_compose
    )
    first_iso = isoformat(first_time)
    merged = merge_entries([], first_entries, now_iso=first_iso)
    meta = {"generated_at": first_iso, "compose_dir": str(config.compose_dir)}
    write_toml(log_path, meta=meta, entries=merged)

    after_first = tomllib.loads(log_path.read_text())
    first_seen = after_first["entries"][0]["first_seen"]

    # Second snapshot
    second_time = datetime(2025, 2, 1, tzinfo=UTC)
    second_entries = await collect_stack_entries(
        config, "svc", now=second_time, run_compose_fn=fake_run_compose
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
