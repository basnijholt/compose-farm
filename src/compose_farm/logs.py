"""Snapshot current compose images into a TOML log."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .config import xdg_config_home
from .executor import run_compose

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from pathlib import Path

    from .config import Config
    from .executor import CommandResult


DEFAULT_LOG_PATH = xdg_config_home() / "compose-farm" / "dockerfarm-log.toml"
DIGEST_HEX_LENGTH = 64


@dataclass(frozen=True)
class SnapshotEntry:
    """Normalized image snapshot for a single service."""

    service: str
    host: str
    compose_file: Path
    image: str
    digest: str
    captured_at: datetime

    def as_dict(self, first_seen: str, last_seen: str) -> dict[str, str]:
        """Render snapshot as a TOML-friendly dict."""
        return {
            "service": self.service,
            "host": self.host,
            "compose_file": str(self.compose_file),
            "image": self.image,
            "digest": self.digest,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_images_output(raw: str) -> list[dict[str, Any]]:
    """Parse `docker compose images --format json` output.

    Handles both a JSON array and newline-separated JSON objects for robustness.
    """
    raw = raw.strip()
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        objects = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            objects.append(json.loads(line))
        return objects

    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _extract_image_fields(record: dict[str, Any]) -> tuple[str, str]:
    """Extract image name and digest with fallbacks."""
    image = record.get("Image") or record.get("Repository") or record.get("Name") or ""
    tag = record.get("Tag") or record.get("Version")
    if tag and ":" not in image.rsplit("/", 1)[-1]:
        image = f"{image}:{tag}"

    digest = (
        record.get("Digest")
        or record.get("Image ID")
        or record.get("ImageID")
        or record.get("ID")
        or ""
    )

    if digest and not digest.startswith("sha256:") and len(digest) == DIGEST_HEX_LENGTH:
        digest = f"sha256:{digest}"

    return image, digest


async def _collect_service_entries(
    config: Config,
    service: str,
    *,
    now: datetime,
    run_compose_fn: Callable[..., Awaitable[CommandResult]] = run_compose,
) -> list[SnapshotEntry]:
    """Run `docker compose images` for a service and normalize results."""
    result = await run_compose_fn(config, service, "images --format json", stream=False)
    if not result.success:
        msg = result.stderr or f"compose images exited with {result.exit_code}"
        error = f"[{service}] Unable to read images: {msg}"
        raise RuntimeError(error)

    records = _parse_images_output(result.stdout)
    # Use first host for snapshots (multi-host services use same images on all hosts)
    host_name = config.get_hosts(service)[0]
    compose_path = config.get_compose_path(service)

    entries: list[SnapshotEntry] = []
    for record in records:
        image, digest = _extract_image_fields(record)
        if not digest:
            continue
        entries.append(
            SnapshotEntry(
                service=service,
                host=host_name,
                compose_file=compose_path,
                image=image,
                digest=digest,
                captured_at=now,
            )
        )
    return entries


def _load_existing_entries(log_path: Path) -> list[dict[str, str]]:
    if not log_path.exists():
        return []
    data = tomllib.loads(log_path.read_text())
    return list(data.get("entries", []))


def _merge_entries(
    existing: Iterable[dict[str, str]],
    new_entries: Iterable[SnapshotEntry],
    *,
    now_iso: str,
) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str], dict[str, str]] = {
        (e["service"], e["host"], e["digest"]): dict(e) for e in existing
    }

    for entry in new_entries:
        key = (entry.service, entry.host, entry.digest)
        first_seen = merged.get(key, {}).get("first_seen", now_iso)
        merged[key] = entry.as_dict(first_seen, now_iso)

    return list(merged.values())


def _write_toml(log_path: Path, *, meta: dict[str, str], entries: list[dict[str, str]]) -> None:
    lines: list[str] = ["[meta]"]
    lines.extend(f'{key} = "{_escape(meta[key])}"' for key in sorted(meta))

    if entries:
        lines.append("")

    for entry in sorted(entries, key=lambda e: (e["service"], e["host"], e["digest"])):
        lines.append("[[entries]]")
        for field in [
            "service",
            "host",
            "compose_file",
            "image",
            "digest",
            "first_seen",
            "last_seen",
        ]:
            value = entry[field]
            lines.append(f'{field} = "{_escape(str(value))}"')
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(content)


async def snapshot_services(
    config: Config,
    services: list[str],
    *,
    log_path: Path | None = None,
    now: datetime | None = None,
    run_compose_fn: Callable[..., Awaitable[CommandResult]] = run_compose,
) -> Path:
    """Capture current image digests for services and write them to a TOML log.

    - Preserves the earliest `first_seen` per (service, host, digest)
    - Updates `last_seen` for digests observed in this snapshot
    - Leaves untouched digests that were not part of this run (history is kept)
    """
    if not services:
        error = "No services specified for snapshot"
        raise RuntimeError(error)

    log_path = log_path or DEFAULT_LOG_PATH
    now_dt = now or datetime.now(UTC)
    now_iso = _isoformat(now_dt)

    existing_entries = _load_existing_entries(log_path)

    snapshot_entries: list[SnapshotEntry] = []
    for service in services:
        snapshot_entries.extend(
            await _collect_service_entries(
                config, service, now=now_dt, run_compose_fn=run_compose_fn
            )
        )

    if not snapshot_entries:
        error = "No image digests were captured"
        raise RuntimeError(error)

    merged_entries = _merge_entries(existing_entries, snapshot_entries, now_iso=now_iso)
    meta = {"generated_at": now_iso, "compose_dir": str(config.compose_dir)}
    _write_toml(log_path, meta=meta, entries=merged_entries)
    return log_path
