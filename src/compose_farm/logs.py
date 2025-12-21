"""Snapshot current compose images into a TOML log."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .executor import run_compose
from .paths import xdg_config_home

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from pathlib import Path

    from .config import Config
    from .executor import CommandResult


DEFAULT_LOG_PATH = xdg_config_home() / "compose-farm" / "dockerfarm-log.toml"
_DIGEST_HEX_LENGTH = 64


@dataclass(frozen=True)
class SnapshotEntry:
    """Normalized image snapshot for a single stack."""

    stack: str
    host: str
    compose_file: Path
    image: str
    digest: str
    captured_at: datetime

    def as_dict(self, first_seen: str, last_seen: str) -> dict[str, str]:
        """Render snapshot as a TOML-friendly dict."""
        return {
            "stack": self.stack,
            "host": self.host,
            "compose_file": str(self.compose_file),
            "image": self.image,
            "digest": self.digest,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }


def isoformat(dt: datetime) -> str:
    """Format a datetime as an ISO 8601 string with Z suffix for UTC."""
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

    if digest and not digest.startswith("sha256:") and len(digest) == _DIGEST_HEX_LENGTH:
        digest = f"sha256:{digest}"

    return image, digest


async def collect_stack_entries(
    config: Config,
    stack: str,
    *,
    now: datetime,
    run_compose_fn: Callable[..., Awaitable[CommandResult]] = run_compose,
) -> list[SnapshotEntry]:
    """Run `docker compose images` for a stack and normalize results."""
    result = await run_compose_fn(config, stack, "images --format json", stream=False)
    if not result.success:
        msg = result.stderr or f"compose images exited with {result.exit_code}"
        error = f"[{stack}] Unable to read images: {msg}"
        raise RuntimeError(error)

    records = _parse_images_output(result.stdout)
    # Use first host for snapshots (multi-host stacks use same images on all hosts)
    host_name = config.get_hosts(stack)[0]
    compose_path = config.get_compose_path(stack)

    entries: list[SnapshotEntry] = []
    for record in records:
        image, digest = _extract_image_fields(record)
        if not digest:
            continue
        entries.append(
            SnapshotEntry(
                stack=stack,
                host=host_name,
                compose_file=compose_path,
                image=image,
                digest=digest,
                captured_at=now,
            )
        )
    return entries


def load_existing_entries(log_path: Path) -> list[dict[str, str]]:
    """Load existing snapshot entries from a TOML log file."""
    if not log_path.exists():
        return []
    data = tomllib.loads(log_path.read_text())
    entries = list(data.get("entries", []))
    normalized: list[dict[str, str]] = []
    for entry in entries:
        normalized_entry = dict(entry)
        if "stack" not in normalized_entry and "service" in normalized_entry:
            normalized_entry["stack"] = normalized_entry.pop("service")
        normalized.append(normalized_entry)
    return normalized


def merge_entries(
    existing: Iterable[dict[str, str]],
    new_entries: Iterable[SnapshotEntry],
    *,
    now_iso: str,
) -> list[dict[str, str]]:
    """Merge new snapshot entries with existing ones, preserving first_seen timestamps."""
    merged: dict[tuple[str, str, str], dict[str, str]] = {
        (e["stack"], e["host"], e["digest"]): dict(e) for e in existing
    }

    for entry in new_entries:
        key = (entry.stack, entry.host, entry.digest)
        first_seen = merged.get(key, {}).get("first_seen", now_iso)
        merged[key] = entry.as_dict(first_seen, now_iso)

    return list(merged.values())


def write_toml(log_path: Path, *, meta: dict[str, str], entries: list[dict[str, str]]) -> None:
    """Write snapshot entries to a TOML log file."""
    lines: list[str] = ["[meta]"]
    lines.extend(f'{key} = "{_escape(meta[key])}"' for key in sorted(meta))

    if entries:
        lines.append("")

    for entry in sorted(entries, key=lambda e: (e["stack"], e["host"], e["digest"])):
        lines.append("[[entries]]")
        for field in [
            "stack",
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
