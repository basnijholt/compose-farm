"""Hatch build hook to vendor CDN assets for offline use.

During wheel builds, this hook:
1. Parses base.html to find elements with data-vendor attributes
2. Downloads each CDN asset to a temporary vendor directory
3. Rewrites base.html to use local /static/vendor/ paths
4. Fetches and bundles license information
5. Includes everything in the wheel via force_include

The source base.html keeps CDN links for development; only the
distributed wheel has vendored assets.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Matches elements with data-vendor attribute: extracts URL and target filename
# Example: <script src="https://..." data-vendor="htmx.js">
# Captures: (1) src/href, (2) URL, (3) attributes between, (4) vendor filename
VENDOR_PATTERN = re.compile(r'(src|href)="(https://[^"]+)"([^>]*?)data-vendor="([^"]+)"')

# License URLs for each package (GitHub raw URLs)
LICENSE_URLS: dict[str, tuple[str, str]] = {
    "htmx": ("MIT", "https://raw.githubusercontent.com/bigskysoftware/htmx/master/LICENSE"),
    "xterm": ("MIT", "https://raw.githubusercontent.com/xtermjs/xterm.js/master/LICENSE"),
    "daisyui": ("MIT", "https://raw.githubusercontent.com/saadeghi/daisyui/master/LICENSE"),
    "tailwindcss": (
        "MIT",
        "https://raw.githubusercontent.com/tailwindlabs/tailwindcss/master/LICENSE",
    ),
}


def _download(url: str) -> bytes:
    """Download a URL, trying urllib first then curl as fallback."""
    # Try urllib first
    try:
        req = Request(  # noqa: S310
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; compose-farm build)"}
        )
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.read()  # type: ignore[no-any-return]
    except Exception:  # noqa: S110
        pass  # Fall through to curl

    # Fallback to curl (handles SSL proxies better)
    result = subprocess.run(
        ["curl", "-fsSL", "--max-time", "30", url],  # noqa: S607
        capture_output=True,
        check=True,
    )
    return bytes(result.stdout)


def _generate_licenses_file(temp_dir: Path) -> None:
    """Download and combine license files into LICENSES.txt."""
    lines = [
        "# Vendored Dependencies - License Information",
        "",
        "This file contains license information for JavaScript/CSS libraries",
        "bundled with compose-farm for offline use.",
        "",
        "=" * 70,
        "",
    ]

    for pkg_name, (license_type, license_url) in LICENSE_URLS.items():
        lines.append(f"## {pkg_name} ({license_type})")
        lines.append(f"Source: {license_url}")
        lines.append("")
        lines.append(_download(license_url).decode("utf-8"))
        lines.append("")
        lines.append("=" * 70)
        lines.append("")

    (temp_dir / "LICENSES.txt").write_text("\n".join(lines))


class VendorAssetsHook(BuildHookInterface):  # type: ignore[misc]
    """Hatch build hook that vendors CDN assets into the wheel."""

    PLUGIN_NAME = "vendor-assets"

    def initialize(
        self,
        _version: str,
        build_data: dict[str, Any],
    ) -> None:
        """Download CDN assets and prepare them for inclusion in the wheel."""
        # Only run for wheel builds
        if self.target_name != "wheel":
            return

        # Paths
        src_dir = Path(self.root) / "src" / "compose_farm"
        base_html_path = src_dir / "web" / "templates" / "base.html"

        if not base_html_path.exists():
            return

        # Create temp directory for vendored assets
        temp_dir = Path(tempfile.mkdtemp(prefix="compose_farm_vendor_"))
        vendor_dir = temp_dir / "vendor"
        vendor_dir.mkdir()

        # Read and parse base.html
        html_content = base_html_path.read_text()
        url_to_filename: dict[str, str] = {}

        # Find all elements with data-vendor attribute and download them
        for match in VENDOR_PATTERN.finditer(html_content):
            url = match.group(2)
            filename = match.group(4)

            if url in url_to_filename:
                continue

            url_to_filename[url] = filename
            content = _download(url)
            (vendor_dir / filename).write_bytes(content)

        if not url_to_filename:
            return

        # Generate LICENSES.txt
        _generate_licenses_file(vendor_dir)

        # Rewrite HTML to use local paths (remove data-vendor, update URL)
        def replace_vendor_tag(match: re.Match[str]) -> str:
            attr = match.group(1)  # src or href
            url = match.group(2)
            between = match.group(3)  # attributes between URL and data-vendor
            filename = match.group(4)
            if url in url_to_filename:
                return f'{attr}="/static/vendor/{filename}"{between}'
            return match.group(0)

        modified_html = VENDOR_PATTERN.sub(replace_vendor_tag, html_content)

        # Write modified base.html to temp
        templates_dir = temp_dir / "templates"
        templates_dir.mkdir()
        (templates_dir / "base.html").write_text(modified_html)

        # Add to force_include to override files in the wheel
        force_include = build_data.setdefault("force_include", {})
        force_include[str(vendor_dir)] = "compose_farm/web/static/vendor"
        force_include[str(templates_dir / "base.html")] = "compose_farm/web/templates/base.html"

        # Store temp_dir path for cleanup
        self._temp_dir = temp_dir

    def finalize(
        self,
        _version: str,
        _build_data: dict[str, Any],
        _artifact_path: str,
    ) -> None:
        """Clean up temporary directory after build."""
        if hasattr(self, "_temp_dir") and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
