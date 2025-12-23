"""Snapshot current compose images into a TOML log."""

from __future__ import annotations

import asyncio
import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .executor import run_command
from .paths import xdg_config_home

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from .config import Config

# Separator used to split output when batching multiple compose commands
_BATCH_SEPARATOR = "---CF-SEP---"


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


async def _collect_stacks_entries_on_host(
    config: Config,
    host_name: str,
    stacks: list[str],
    *,
    now: datetime,
) -> list[SnapshotEntry]:
    """Collect image entries for multiple stacks on one host in a single SSH call."""
    if not stacks:
        return []

    host = config.hosts[host_name]

    # Build batched command: echo separator+stack, then docker compose images
    commands = [
        f"echo '{_BATCH_SEPARATOR}{s}' && "
        f"docker compose -f {config.get_compose_path(s)} images --format json 2>/dev/null || true"
        for s in stacks
    ]
    result = await run_command(host, "; ".join(commands), host_name, stream=False, prefix="")

    if not result.success:
        return []

    # Parse batched output: separator lines mark stack boundaries
    entries: list[SnapshotEntry] = []
    current_stack: str | None = None
    current_output: list[str] = []

    def flush_stack() -> None:
        if current_stack and current_output:
            for record in _parse_images_output("\n".join(current_output)):
                image, digest = _extract_image_fields(record)
                if digest:
                    entries.append(
                        SnapshotEntry(
                            stack=current_stack,
                            host=host_name,
                            compose_file=config.get_compose_path(current_stack),
                            image=image,
                            digest=digest,
                            captured_at=now,
                        )
                    )

    for line in result.stdout.splitlines():
        if line.startswith(_BATCH_SEPARATOR):
            flush_stack()
            current_stack = line[len(_BATCH_SEPARATOR) :]
            current_output = []
        elif current_stack is not None:
            current_output.append(line)

    flush_stack()
    return entries


async def collect_all_stacks_entries(
    config: Config,
    stacks_by_host: dict[str, list[str]],
    *,
    now: datetime,
) -> list[SnapshotEntry]:
    """Collect image entries for all stacks, batched by host.

    This makes only 1 SSH call per host instead of 1 per stack.

    Args:
        config: Configuration
        stacks_by_host: Dict mapping host_name -> list of stacks on that host
        now: Timestamp for the snapshot entries

    Returns:
        List of SnapshotEntry for all stacks.

    """
    tasks = [
        _collect_stacks_entries_on_host(config, host, stacks, now=now)
        for host, stacks in stacks_by_host.items()
        if stacks
    ]

    if not tasks:
        return []

    results = await asyncio.gather(*tasks)
    return [entry for entries in results for entry in entries]


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
