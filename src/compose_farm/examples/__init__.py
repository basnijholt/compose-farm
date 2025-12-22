"""Example stack templates for compose-farm."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

# All available examples: name -> description
# "full" is special: multi-stack setup with config file
EXAMPLES = {
    "whoami": "Simple HTTP service that returns hostname (great for testing Traefik)",
    "nginx": "Basic nginx web server with static files",
    "postgres": "PostgreSQL database with persistent volume",
    "full": "Complete setup with Traefik + whoami (includes compose-farm.yaml)",
}

# Examples that are single stacks (everything except "full")
SINGLE_STACK_EXAMPLES = {k: v for k, v in EXAMPLES.items() if k != "full"}


def list_example_files(name: str) -> list[tuple[str, str]]:
    """List files in an example template, returning (relative_path, content) tuples."""
    if name not in EXAMPLES:
        msg = f"Unknown example: {name}. Available: {', '.join(EXAMPLES.keys())}"
        raise ValueError(msg)

    example_dir = resources.files("compose_farm.examples") / name
    example_path = Path(str(example_dir))
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
