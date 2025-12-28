"""CDN asset definitions and caching for tests and demo recordings.

This module provides a single source of truth for CDN asset URLs used in
browser tests and demo recordings. Assets are intercepted and served from
a local cache to eliminate network variability.

Note: The canonical list of CDN assets for production is in base.html
(with data-vendor attributes). This module includes those plus dynamically
loaded assets (like Monaco editor modules loaded by app.js).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# CDN assets to cache locally for tests/demos
# Format: URL -> (local_filename, content_type)
#
# If tests fail with "Uncached CDN request", add the URL here.
CDN_ASSETS: dict[str, tuple[str, str]] = {
    # From base.html (data-vendor attributes)
    "https://cdn.jsdelivr.net/npm/daisyui@5/themes.css": ("daisyui-themes.css", "text/css"),
    "https://cdn.jsdelivr.net/npm/daisyui@5": ("daisyui.css", "text/css"),
    "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4": (
        "tailwind.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css": ("xterm.css", "text/css"),
    "https://unpkg.com/htmx.org@2.0.4": ("htmx.js", "application/javascript"),
    "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js": (
        "xterm.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js": (
        "xterm-fit.js",
        "application/javascript",
    ),
    "https://unpkg.com/idiomorph/dist/idiomorph.min.js": (
        "idiomorph.js",
        "application/javascript",
    ),
    "https://unpkg.com/idiomorph/dist/idiomorph-ext.min.js": (
        "idiomorph-ext.js",
        "application/javascript",
    ),
    # Monaco editor - dynamically loaded by app.js
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js": (
        "monaco-loader.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/editor/editor.main.js": (
        "monaco-editor-main.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/editor/editor.main.css": (
        "monaco-editor-main.css",
        "text/css",
    ),
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/base/worker/workerMain.js": (
        "monaco-workerMain.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/basic-languages/yaml/yaml.js": (
        "monaco-yaml.js",
        "application/javascript",
    ),
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/base/browser/ui/codicons/codicon/codicon.ttf": (
        "monaco-codicon.ttf",
        "font/ttf",
    ),
}


def download_url(url: str) -> bytes | None:
    """Download URL content using curl."""
    try:
        result = subprocess.run(
            ["curl", "-fsSL", "--max-time", "30", url],  # noqa: S607
            capture_output=True,
            check=True,
        )
        return bytes(result.stdout)
    except Exception:
        return None


def ensure_vendor_cache(cache_dir: Path) -> Path:
    """Download CDN assets to cache directory if not already present.

    Args:
        cache_dir: Directory to store cached assets.

    Returns:
        The cache directory path.

    Raises:
        RuntimeError: If any asset fails to download.

    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    for url, (filename, _content_type) in CDN_ASSETS.items():
        filepath = cache_dir / filename
        if filepath.exists():
            continue
        content = download_url(url)
        if not content:
            msg = f"Failed to download {url} - check network/curl"
            raise RuntimeError(msg)
        filepath.write_bytes(content)

    return cache_dir
