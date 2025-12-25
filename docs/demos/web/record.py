#!/usr/bin/env python3
"""Record all web UI demos.

This script orchestrates recording of web UI demos using Playwright,
then converts the WebM recordings to GIF format.

Usage:
    python docs/demos/web/record.py           # Record all demos
    python docs/demos/web/record.py navigation  # Record specific demo

Requirements:
    - Playwright with Chromium: playwright install chromium
    - ffmpeg for GIF conversion: apt install ffmpeg / brew install ffmpeg
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()

SCRIPT_DIR = Path(__file__).parent
REPO_DIR = SCRIPT_DIR.parent.parent.parent
OUTPUT_DIR = REPO_DIR / "docs" / "assets"

DEMOS = [
    "navigation",
    "stack",
    "themes",
    "workflow",
    "console",
    "shell",
    "live_stats",
]

# High-quality ffmpeg settings for VP8 encoding
# See: https://github.com/microsoft/playwright/issues/10855
# See: https://github.com/microsoft/playwright/issues/31424
#
# MAX_QUALITY: Lossless-like, largest files
# BALANCED_QUALITY: ~43% file size, nearly indistinguishable quality
MAX_QUALITY_ARGS = "-c:v vp8 -qmin 0 -qmax 0 -crf 0 -deadline best -speed 0 -b:v 0 -threads 0"
BALANCED_QUALITY_ARGS = "-c:v vp8 -qmin 0 -qmax 10 -crf 4 -deadline best -speed 0 -b:v 0 -threads 0"

# Choose which quality to use
VIDEO_QUALITY_ARGS = MAX_QUALITY_ARGS


def patch_playwright_video_quality() -> None:
    """Patch Playwright's videoRecorder.js to use high-quality encoding settings."""
    from playwright._impl._driver import compute_driver_executable  # noqa: PLC0415

    # compute_driver_executable returns (node_path, cli_path)
    result = compute_driver_executable()
    node_path = result[0] if isinstance(result, tuple) else result
    driver_path = Path(node_path).parent

    video_recorder = driver_path / "package" / "lib" / "server" / "chromium" / "videoRecorder.js"

    if not video_recorder.exists():
        msg = f"videoRecorder.js not found at {video_recorder}"
        raise FileNotFoundError(msg)

    content = video_recorder.read_text()

    # Check if already patched
    if "deadline best" in content:
        return  # Already patched

    # Pattern to match the ffmpeg args line
    pattern = (
        r"-c:v vp8 -qmin \d+ -qmax \d+ -crf \d+ -deadline \w+ -speed \d+ -b:v \w+ -threads \d+"
    )

    if not re.search(pattern, content):
        msg = "Could not find ffmpeg args pattern in videoRecorder.js"
        raise ValueError(msg)

    # Replace with high-quality settings
    new_content = re.sub(pattern, VIDEO_QUALITY_ARGS, content)
    video_recorder.write_text(new_content)
    console.print("[green]Patched Playwright for high-quality video recording[/green]")


def record_demo(name: str, index: int, total: int) -> Path | None:
    """Run a single demo and return the video path."""
    console.print(f"[cyan][{index}/{total}][/cyan] [green]Recording:[/green] web-{name}")

    demo_file = SCRIPT_DIR / f"demo_{name}.py"
    if not demo_file.exists():
        console.print(f"[red]  Demo file not found: {demo_file}[/red]")
        return None

    # Create temp output dir for this recording
    temp_dir = SCRIPT_DIR / ".recordings"
    temp_dir.mkdir(exist_ok=True)

    # Run pytest with video recording
    # Set PYTHONPATH so conftest.py imports work
    env = {**os.environ, "PYTHONPATH": str(SCRIPT_DIR)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(demo_file),
            "-v",
            "--no-cov",
            "-x",  # Stop on first failure
            f"--basetemp={temp_dir}",
        ],
        check=False,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        console.print(f"[red]  Failed to record {name}[/red]")
        console.print(result.stdout)
        console.print(result.stderr)
        return None

    # Find the recorded video
    videos = list(temp_dir.rglob("*.webm"))
    if not videos:
        console.print(f"[red]  No video found for {name}[/red]")
        return None

    # Use the most recent video
    video = max(videos, key=lambda p: p.stat().st_mtime)
    console.print(f"[green]  Recorded: {video.name}[/green]")
    return video


def convert_to_gif(webm_path: Path, output_name: str) -> Path:
    """Convert WebM to GIF using ffmpeg with palette optimization."""
    gif_path = OUTPUT_DIR / f"{output_name}.gif"
    palette_path = webm_path.parent / "palette.png"

    # Two-pass approach for better quality
    # Pass 1: Generate palette
    subprocess.run(
        [  # noqa: S607
            "ffmpeg",
            "-y",
            "-i",
            str(webm_path),
            "-vf",
            "fps=10,scale=1280:-1:flags=lanczos,palettegen=stats_mode=diff",
            str(palette_path),
        ],
        check=True,
        capture_output=True,
    )

    # Pass 2: Generate GIF with palette
    subprocess.run(
        [  # noqa: S607
            "ffmpeg",
            "-y",
            "-i",
            str(webm_path),
            "-i",
            str(palette_path),
            "-lavfi",
            "fps=10,scale=1280:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
            str(gif_path),
        ],
        check=True,
        capture_output=True,
    )

    palette_path.unlink(missing_ok=True)
    return gif_path


def move_recording(video_path: Path, name: str) -> tuple[Path, Path]:
    """Move WebM and convert to GIF, returning both paths."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_name = f"web-{name}"
    webm_dest = OUTPUT_DIR / f"{output_name}.webm"

    shutil.copy2(video_path, webm_dest)
    console.print(f"[blue]  WebM: {webm_dest.relative_to(REPO_DIR)}[/blue]")

    gif_path = convert_to_gif(video_path, output_name)
    console.print(f"[blue]  GIF:  {gif_path.relative_to(REPO_DIR)}[/blue]")

    return webm_dest, gif_path


def cleanup() -> None:
    """Clean up temporary recording files."""
    temp_dir = SCRIPT_DIR / ".recordings"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


def main() -> int:
    """Record all web UI demos."""
    console.print("[blue]Recording web UI demos...[/blue]")
    console.print(f"Output directory: {OUTPUT_DIR}")
    console.print()

    # Patch Playwright for high-quality video recording
    patch_playwright_video_quality()

    # Determine which demos to record
    if len(sys.argv) > 1:
        demos_to_record = [d for d in sys.argv[1:] if d in DEMOS]
        if not demos_to_record:
            console.print(f"[red]Unknown demo(s). Available: {', '.join(DEMOS)}[/red]")
            return 1
    else:
        demos_to_record = DEMOS

    results: dict[str, tuple[Path | None, Path | None]] = {}

    try:
        for i, demo in enumerate(demos_to_record, 1):
            video_path = record_demo(demo, i, len(demos_to_record))
            if video_path:
                webm, gif = move_recording(video_path, demo)
                results[demo] = (webm, gif)
            else:
                results[demo] = (None, None)
            console.print()
    finally:
        cleanup()

    # Summary
    console.print("[blue]=== Summary ===[/blue]")
    success_count = sum(1 for w, _ in results.values() if w is not None)
    console.print(f"Recorded: {success_count}/{len(demos_to_record)} demos")
    console.print()

    for demo, (webm, gif) in results.items():  # type: ignore[assignment]
        status = "[green]OK[/green]" if webm else "[red]FAILED[/red]"
        console.print(f"  {demo}: {status}")
        if webm:
            console.print(f"    {webm.relative_to(REPO_DIR)}")
        if gif:
            console.print(f"    {gif.relative_to(REPO_DIR)}")

    return 0 if success_count == len(demos_to_record) else 1


if __name__ == "__main__":
    sys.exit(main())
