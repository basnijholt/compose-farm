"""Tests for snapshot logging."""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from compose_farm.config import Config, Host
from compose_farm.executor import CommandResult
from compose_farm.logs import _parse_images_output, snapshot_services


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
    service_dir = compose_dir / "svc"
    service_dir.mkdir()
    (service_dir / "docker-compose.yml").write_text("services: {}\n")

    config = Config(
        compose_dir=compose_dir,
        hosts={"local": Host(address="localhost")},
        services={"svc": "local"},
    )

    sample_output = json.dumps([{"Service": "svc", "Image": "redis", "Digest": "sha256:abc"}])

    async def fake_run_compose(
        _cfg: Config, service: str, compose_cmd: str, *, stream: bool = True
    ) -> CommandResult:
        assert compose_cmd == "images --format json"
        assert stream is False or stream is True
        return CommandResult(
            service=service,
            exit_code=0,
            success=True,
            stdout=sample_output,
            stderr="",
        )

    log_path = tmp_path / "dockerfarm-log.toml"

    first_time = datetime(2025, 1, 1, tzinfo=UTC)
    await snapshot_services(
        config,
        ["svc"],
        log_path=log_path,
        now=first_time,
        run_compose_fn=fake_run_compose,
    )

    after_first = tomllib.loads(log_path.read_text())
    first_seen = after_first["entries"][0]["first_seen"]

    second_time = datetime(2025, 2, 1, tzinfo=UTC)
    await snapshot_services(
        config,
        ["svc"],
        log_path=log_path,
        now=second_time,
        run_compose_fn=fake_run_compose,
    )

    after_second = tomllib.loads(log_path.read_text())
    entry = after_second["entries"][0]
    assert entry["first_seen"] == first_seen
    assert entry["last_seen"].startswith("2025-02-01")
