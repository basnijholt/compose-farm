"""Test CLI startup performance."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

# Thresholds in seconds, per OS
if sys.platform == "darwin":
    CLI_STARTUP_THRESHOLD = 0.35
else:  # Linux
    CLI_STARTUP_THRESHOLD = 0.25


@pytest.mark.skipif(
    "PYTEST_XDIST_WORKER" in os.environ,
    reason="Skip in parallel mode due to resource contention",
)
def test_cli_startup_time() -> None:
    """Verify CLI startup time stays within acceptable bounds.

    This test ensures we don't accidentally introduce slow imports
    that degrade the user experience.
    """
    cf_path = shutil.which("cf")
    assert cf_path is not None, "cf command not found in PATH"

    # Run up to 6 times, return early if we hit the threshold
    times: list[float] = []
    for _ in range(6):
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

        # Pass early if under threshold
        if elapsed < CLI_STARTUP_THRESHOLD:
            print(f"\nCLI startup: {elapsed:.3f}s (threshold: {CLI_STARTUP_THRESHOLD}s)")
            return

    # All attempts exceeded threshold
    best_time = min(times)
    msg = (
        f"\nCLI startup times: {[f'{t:.3f}s' for t in times]}\n"
        f"Best: {best_time:.3f}s, Threshold: {CLI_STARTUP_THRESHOLD}s"
    )
    print(msg)

    err_msg = f"CLI startup too slow!\n{msg}\nCheck for slow imports."
    raise AssertionError(err_msg)
