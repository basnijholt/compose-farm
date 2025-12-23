"""Snapshot current compose images into a TOML log."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .executor import run_command
from .paths import xdg_config_home

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from .config import Config

# Separator used to split output sections
_SECTION_SEPARATOR = "---CF-SEP---"


DEFAULT_LOG_PATH = xdg_config_home() / "compose-farm" / "dockerfarm-log.toml"


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


def _parse_image_digests(image_json: str) -> dict[str, str]:
    """Parse docker image inspect JSON to build image tag -> digest map."""
    if not image_json:
        return {}
    try:
        image_data = json.loads(image_json)
    except json.JSONDecodeError:
        return {}

    image_digests: dict[str, str] = {}
    for img in image_data:
        tags = img.get("RepoTags") or []
        digests = img.get("RepoDigests") or []
        digest = digests[0].split("@")[-1] if digests else img.get("Id", "")
        for tag in tags:
            image_digests[tag] = digest
        if img.get("Id"):
            image_digests[img["Id"]] = digest
    return image_digests


async def collect_stacks_entries_on_host(
    config: Config,
    host_name: str,
    stacks: set[str],
    *,
    now: datetime,
) -> list[SnapshotEntry]:
    """Collect image entries for stacks on one host using 2 docker commands.

    Uses `docker ps` to get running containers + their compose project labels,
    then `docker image inspect` to get digests for all unique images.
    Much faster than running N `docker compose images` commands.
    """
    if not stacks:
        return []

    host = config.hosts[host_name]

    # Single SSH call with 2 docker commands:
    # 1. Get project|image pairs from running containers
    # 2. Get image info (including digests) for all unique images
    command = (
        f"docker ps --format '{{{{.Label \"com.docker.compose.project\"}}}}|{{{{.Image}}}}' && "
        f"echo '{_SECTION_SEPARATOR}' && "
        "docker image inspect $(docker ps --format '{{.Image}}' | sort -u) 2>/dev/null || true"
    )
    result = await run_command(host, command, host_name, stream=False, prefix="")

    if not result.success:
        return []

    # Split output into two sections
    parts = result.stdout.split(_SECTION_SEPARATOR)
    if len(parts) != 2:  # noqa: PLR2004
        return []

    container_lines, image_json = parts[0].strip(), parts[1].strip()

    # Parse project|image pairs, filtering to only stacks we care about
    stack_images: dict[str, set[str]] = {}
    for line in container_lines.splitlines():
        if "|" not in line:
            continue
        project, image = line.split("|", 1)
        if project in stacks:
            stack_images.setdefault(project, set()).add(image)

    if not stack_images:
        return []

    # Parse image inspect JSON to build image -> digest map
    image_digests = _parse_image_digests(image_json)

    # Build entries
    entries: list[SnapshotEntry] = []
    for stack, images in stack_images.items():
        for image in images:
            digest = image_digests.get(image, "")
            if digest:
                entries.append(
                    SnapshotEntry(
                        stack=stack,
                        host=host_name,
                        compose_file=config.get_compose_path(stack),
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
