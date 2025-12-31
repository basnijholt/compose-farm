"""CDN asset definitions and caching for tests and demo recordings.

This module provides CDN asset URLs used in browser tests and demo recordings.
Assets are intercepted and served from a local cache to eliminate network
variability.

The canonical list of CDN assets is in vendor-assets.json. This module loads
that file and provides the CDN_ASSETS dict for test caching.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _load_cdn_assets() -> dict[str, tuple[str, str]]:
    """Load CDN assets from vendor-assets.json.

    Returns:
        Dict mapping URL to (filename, content_type) tuple.

    """
    json_path = Path(__file__).parent / "vendor-assets.json"
    with json_path.open() as f:
        config = json.load(f)

    return {asset["url"]: (asset["filename"], asset["content_type"]) for asset in config["assets"]}


# CDN assets to cache locally for tests/demos
# Format: URL -> (local_filename, content_type)
#
# If tests fail with "Uncached CDN request", add the URL to vendor-assets.json.
CDN_ASSETS: dict[str, tuple[str, str]] = _load_cdn_assets()


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
        filepath.parent.mkdir(parents=True, exist_ok=True)
        content = download_url(url)
        if not content:
            msg = f"Failed to download {url} - check network/curl"
            raise RuntimeError(msg)
        filepath.write_bytes(content)

    return cache_dir
