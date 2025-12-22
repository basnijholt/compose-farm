"""Example stack templates for compose-farm."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

# Simple examples: single stack templates
EXAMPLES = {
    "whoami": "Simple HTTP service that returns hostname (great for testing Traefik)",
    "nginx": "Basic nginx web server with static files",
    "postgres": "PostgreSQL database with persistent volume",
}

# Full example: complete multi-stack setup with config
FULL_EXAMPLE = "full"
FULL_EXAMPLE_DESC = "Complete setup with Traefik + whoami (includes compose-farm.yaml)"


def get_example_path(name: str) -> Path:
    """Get the path to an example template directory."""
    if name != FULL_EXAMPLE and name not in EXAMPLES:
        msg = f"Unknown example: {name}. Available: {', '.join(EXAMPLES.keys())}, {FULL_EXAMPLE}"
        raise ValueError(msg)

    example_dir = resources.files("compose_farm.examples") / name
    return Path(str(example_dir))


def list_example_files(name: str) -> list[tuple[str, str]]:
    """List files in an example template, returning (relative_path, content) tuples."""
    example_path = get_example_path(name)
    files: list[tuple[str, str]] = []

    def walk_dir(directory: Path, prefix: str = "") -> None:
        for item in sorted(directory.iterdir()):
            rel_path = f"{prefix}{item.name}" if prefix else item.name
            if item.is_file():
                content = item.read_text(encoding="utf-8")
                files.append((rel_path, content))
            elif item.is_dir() and not item.name.startswith("__"):
                walk_dir(item, f"{rel_path}/")

    walk_dir(example_path)
    return files
