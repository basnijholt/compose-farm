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
OUTPUT_DIR = SCRIPT_DIR.parent.parent / "assets"

DEMOS = ["install", "quickstart", "logs", "update", "migration", "apply"]


def _run(cmd: list[str], **kw) -> bool:
    return subprocess.run(cmd, check=False, **kw).returncode == 0


def _record(name: str) -> bool:
    console.print(f"[green]Recording:[/green] {name}")
    if _run(["vhs", str(SCRIPT_DIR / f"{name}.tape")], cwd=STACKS_DIR):
        console.print("[green]  ✓ Done[/green]")
        return True
    console.print("[red]  ✗ Failed[/red]")
    return False


def _check_state(demo: str) -> bool:
    """Check if state is correct for demo. Returns False if demo should be skipped."""
    if demo not in ("migration", "apply"):
        return True

    config = load_config()
    state = load_state(config)
    state_host = state.get("audiobookshelf")
    config_host = config.stacks.get("audiobookshelf")

    # Migration needs audiobookshelf on nas in BOTH config and state
    if demo == "migration":
        if config_host != "nas":
            console.print(
                f"[red]Skipping {demo}: config has audiobookshelf on '{config_host}', needs 'nas'[/red]"
            )
            console.print(
                "[yellow]Fix: sed -i 's/audiobookshelf: .*/audiobookshelf: nas/' /opt/stacks/compose-farm.yaml[/yellow]"
            )
            return False
        if state_host != "nas":
            console.print(
                f"[red]Skipping {demo}: audiobookshelf running on '{state_host}', needs 'nas'[/red]"
            )
            console.print("[yellow]Fix: cf apply[/yellow]")
            return False

    if demo == "apply" and state_host != "nas":
        console.print(
            f"[red]Skipping {demo}: audiobookshelf is on '{state_host}', needs 'nas'[/red]"
        )
        return False

    return True


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

    for demo in demos:
        if not _check_state(demo):
            return 1

        if not _record(demo):
            return 1

        # Reset after migration demo
        if demo == "migration":
            _run(
                [
                    "sed",
                    "-i",
                    "s/audiobookshelf: anton/audiobookshelf: nas/",
                    str(STACKS_DIR / "compose-farm.yaml"),
                ]
            )
            _run(["cf", "apply"], cwd=STACKS_DIR)

    # Move outputs
    OUTPUT_DIR.mkdir(exist_ok=True)
    for f in (STACKS_DIR / "docs/assets").glob("*.[gw]*"):
        shutil.move(str(f), str(OUTPUT_DIR / f.name))

    console.print(f"\n[green]Done![/green] Saved to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
