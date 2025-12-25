"""Standalone Live Stats demo recording (bypasses pytest-playwright).

This script directly uses Playwright to record the Live Stats page demo,
avoiding pytest-playwright fixtures which were causing issues with inline
scripts not loading.

Usage:
    python docs/demos/web/record_live_stats_standalone.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# Configuration
SERVER_URL = "http://127.0.0.1:9001"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "assets"


def pause(ms: int = 500) -> None:
    """Pause for visibility in recording.

    Uses time.sleep instead of page.wait_for_timeout for more reliable
    timing during video recording.
    """
    time.sleep(ms / 1000)


def slow_type(page: Page, selector: str, text: str, delay: int = 100) -> None:
    """Type with visible delay between keystrokes."""
    page.type(selector, text, delay=delay)


def main() -> int:
    """Record the Live Stats page demo and convert to GIF."""
    with tempfile.TemporaryDirectory() as tmpdir:
        video_dir = Path(tmpdir)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        print("Recording Live Stats demo...")

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which("chromium"))
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            # Navigate directly to Live Stats
            # Note: Can't use command palette (HTMX boost) because inline scripts
            # aren't executed when HTMX swaps content
            page.goto(f"{SERVER_URL}/live-stats")

            # Wait for containers to load
            page.wait_for_selector("#container-rows tr:not(:has(.loading))", timeout=30000)
            pause(2000)

            # Verify timer is working
            timer = page.locator("#refresh-timer")
            timer_text = timer.text_content()
            print(f"Timer text: {timer_text!r}")

            # Filter containers
            slow_type(page, "#filter-input", "jelly", delay=100)
            pause(1500)

            # Clear filter
            page.fill("#filter-input", "")
            pause(1000)

            # Watch auto-refresh timer count down
            pause(4000)  # Watch timer countdown and refresh

            # Final pause
            pause(1000)

            # Close and get video path
            page.close()
            context.close()
            browser.close()

        # Find the recorded video
        videos = list(video_dir.glob("*.webm"))
        if not videos:
            print("ERROR: No video found!")
            return 1

        video = max(videos, key=lambda p: p.stat().st_mtime)
        print(f"Recorded: {video}")

        # Copy WebM
        webm_dest = OUTPUT_DIR / "web-live_stats.webm"
        shutil.copy2(video, webm_dest)
        print(f"WebM: {webm_dest}")

        # Convert to GIF using ffmpeg
        gif_dest = OUTPUT_DIR / "web-live_stats.gif"
        palette = video_dir / "palette.png"

        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video),
                "-vf",
                "fps=10,scale=1280:-1:flags=lanczos,palettegen=stats_mode=diff",
                str(palette),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video),
                "-i",
                str(palette),
                "-lavfi",
                "fps=10,scale=1280:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                str(gif_dest),
            ],
            check=True,
            capture_output=True,
        )
        print(f"GIF: {gif_dest}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
