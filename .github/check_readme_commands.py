#!/usr/bin/env python3
"""Check that all CLI commands are documented in the README."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typer

from compose_farm.cli import app


def get_all_commands(typer_app: typer.Typer, prefix: str = "cf") -> set[str]:
    """Extract all command names from a Typer app, including nested subcommands."""
    commands = set()

    # Get registered commands (skip hidden ones like aliases)
    for command in typer_app.registered_commands:
        if command.hidden:
            continue
        name = command.name
        if not name and command.callback:
            name = getattr(command.callback, "__name__", None)
        if name:
            commands.add(f"{prefix} {name}")

    # Get registered sub-apps (like 'config')
    for group in typer_app.registered_groups:
        sub_app = group.typer_instance
        sub_name = group.name
        if sub_app and sub_name:
            commands.add(f"{prefix} {sub_name}")
            # Don't recurse into subcommands - we only document the top-level subcommand

    return commands


def get_documented_commands(readme_path: Path) -> set[str]:
    """Extract commands documented in README from help output sections."""
    content = readme_path.read_text()

    # Match patterns like: <code>cf command --help</code>
    pattern = r"<code>(cf\s+[\w-]+)\s+--help</code>"
    matches = re.findall(pattern, content)

    return set(matches)


def main() -> int:
    """Check that all CLI commands are documented in the README."""
    readme_path = Path(__file__).parent.parent / "README.md"

    if not readme_path.exists():
        print(f"ERROR: README.md not found at {readme_path}")
        return 1

    cli_commands = get_all_commands(app)
    documented_commands = get_documented_commands(readme_path)

    # Also check for the main 'cf' help
    if "<code>cf --help</code>" in readme_path.read_text():
        documented_commands.add("cf")
    cli_commands.add("cf")

    missing = cli_commands - documented_commands
    extra = documented_commands - cli_commands

    if missing or extra:
        if missing:
            print("ERROR: Commands missing from README --help documentation:")
            for cmd in sorted(missing):
                print(f"  - {cmd}")
        if extra:
            print("WARNING: Commands documented but not in CLI:")
            for cmd in sorted(extra):
                print(f"  - {cmd}")
        return 1

    print(f"✓ All {len(cli_commands)} commands documented in README")
    return 0


if __name__ == "__main__":
    sys.exit(main())
