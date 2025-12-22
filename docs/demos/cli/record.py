#!/usr/bin/env python3
"""Record CLI demos using VHS."""

import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from compose_farm.config import load_config
from compose_farm.state import load_state

console = Console()
SCRIPT_DIR = Path(__file__).parent
STACKS_DIR = Path("/opt/stacks")
CONFIG_FILE = STACKS_DIR / "compose-farm.yaml"
OUTPUT_DIR = SCRIPT_DIR.parent.parent / "assets"

DEMOS = ["install", "quickstart", "logs", "compose", "update", "migration", "apply"]


def _run(cmd: list[str], **kw) -> bool:
    return subprocess.run(cmd, check=False, **kw).returncode == 0


def _set_config(host: str) -> None:
    """Set audiobookshelf host in config file."""
    _run(["sed", "-i", f"s/audiobookshelf: .*/audiobookshelf: {host}/", str(CONFIG_FILE)])


def _get_hosts() -> tuple[str | None, str | None]:
    """Return (config_host, state_host) for audiobookshelf."""
    config = load_config()
    state = load_state(config)
    return config.stacks.get("audiobookshelf"), state.get("audiobookshelf")


def _setup_state(demo: str) -> bool:
    """Set up required state for demo. Returns False on failure."""
    if demo not in ("migration", "apply"):
        return True

    config_host, state_host = _get_hosts()

    if demo == "migration":
        # Migration needs audiobookshelf on nas in BOTH config and state
        if config_host != "nas":
            console.print("[yellow]Setting up: config → nas[/yellow]")
            _set_config("nas")
        if state_host != "nas":
            console.print("[yellow]Setting up: state → nas[/yellow]")
            if not _run(["cf", "apply"], cwd=STACKS_DIR):
                return False

    elif demo == "apply":
        # Apply needs config=nas, state=anton (so there's something to apply)
        if config_host != "nas":
            console.print("[yellow]Setting up: config → nas[/yellow]")
            _set_config("nas")
        if state_host == "nas":
            console.print("[yellow]Setting up: state → anton[/yellow]")
            _set_config("anton")
            if not _run(["cf", "apply"], cwd=STACKS_DIR):
                return False
            _set_config("nas")

    return True


def _record(name: str, index: int, total: int) -> bool:
    """Record a single demo."""
    console.print(f"[cyan][{index}/{total}][/cyan] [green]Recording:[/green] {name}")
    if _run(["vhs", str(SCRIPT_DIR / f"{name}.tape")], cwd=STACKS_DIR):
        console.print("[green]  ✓ Done[/green]")
        return True
    console.print("[red]  ✗ Failed[/red]")
    return False


def _reset_after(demo: str, next_demo: str | None) -> None:
    """Reset state after demos that modify audiobookshelf."""
    if demo not in ("quickstart", "migration"):
        return
    _set_config("nas")
    if next_demo != "apply":  # Let apply demo show the migration
        _run(["cf", "apply"], cwd=STACKS_DIR)


def _restore_config(original: str) -> None:
    """Restore original config and sync state."""
    console.print("[yellow]Restoring original config...[/yellow]")
    CONFIG_FILE.write_text(original)
    _run(["cf", "apply"], cwd=STACKS_DIR)


def _main() -> int:
    if not shutil.which("vhs"):
        console.print("[red]VHS not found. Install: brew install vhs[/red]")
        return 1

    if not _run(["git", "-C", str(STACKS_DIR), "diff", "--quiet", "compose-farm.yaml"]):
        console.print("[red]compose-farm.yaml has uncommitted changes[/red]")
        return 1

    demos = [d for d in sys.argv[1:] if d in DEMOS] or DEMOS
    if sys.argv[1:] and not demos:
        console.print(f"[red]Unknown demo. Available: {', '.join(DEMOS)}[/red]")
        return 1

    # Save original config to restore after recording
    original_config = CONFIG_FILE.read_text()

    try:
        for i, demo in enumerate(demos, 1):
            if not _setup_state(demo):
                return 1
            if not _record(demo, i, len(demos)):
                return 1
            _reset_after(demo, demos[i] if i < len(demos) else None)
    finally:
        _restore_config(original_config)

    # Move outputs
    OUTPUT_DIR.mkdir(exist_ok=True)
    for f in (STACKS_DIR / "docs/assets").glob("*.[gw]*"):
        shutil.move(str(f), str(OUTPUT_DIR / f.name))

    console.print(f"\n[green]Done![/green] Saved to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
