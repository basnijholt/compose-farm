"""Test CLI startup performance."""

from __future__ import annotations

import shutil
import subprocess
import time

# Threshold in seconds (0.35s to accommodate slower CI runners like macOS/Windows)
CLI_STARTUP_THRESHOLD = 0.35


def test_cli_startup_time() -> None:
    """Verify CLI startup time stays within acceptable bounds.

    This test ensures we don't accidentally introduce slow imports
    that degrade the user experience.
    """
    cf_path = shutil.which("cf")
    assert cf_path is not None, "cf command not found in PATH"

    # Run multiple times and take the minimum to reduce noise
    times: list[float] = []
    for _ in range(3):
        start = time.perf_counter()
        result = subprocess.run(
            [cf_path, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - start
        times.append(elapsed)

        # Verify the command succeeded
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

    best_time = min(times)
    avg_time = sum(times) / len(times)

    # Always print timing info - visible in CI logs even on failure
    msg = (
        f"\nCLI startup times: {[f'{t:.3f}s' for t in times]}\n"
        f"Best: {best_time:.3f}s, Avg: {avg_time:.3f}s, Threshold: {CLI_STARTUP_THRESHOLD}s"
    )
    print(msg)

    assert best_time < CLI_STARTUP_THRESHOLD, (
        f"CLI startup too slow!\n{msg}\nCheck for slow imports."
    )
