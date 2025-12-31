"""Hatch build hook to vendor CDN assets for offline use.

During wheel builds, this hook:
1. Reads vendor-assets.json to find assets marked for vendoring
2. Downloads each CDN asset to a temporary vendor directory
3. Rewrites base.html to use local /static/vendor/ paths
4. Fetches and bundles license information
5. Includes everything in the wheel via force_include

The source base.html keeps CDN links for development; only the
distributed wheel has vendored assets.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


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


def _load_vendor_assets(root: Path) -> dict[str, Any]:
    """Load vendor-assets.json from the web module."""
    json_path = root / "src" / "compose_farm" / "web" / "vendor-assets.json"
    with json_path.open() as f:
        return json.load(f)


def _generate_licenses_file(temp_dir: Path, licenses: dict[str, dict[str, str]]) -> None:
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

    for pkg_name, license_info in licenses.items():
        license_type = license_info["type"]
        license_url = license_info["url"]
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

        # Load vendor assets configuration
        vendor_config = _load_vendor_assets(Path(self.root))
        assets_to_vendor = vendor_config["assets"]

        if not assets_to_vendor:
            return

        # Create temp directory for vendored assets
        temp_dir = Path(tempfile.mkdtemp(prefix="compose_farm_vendor_"))
        vendor_dir = temp_dir / "vendor"
        vendor_dir.mkdir()

        # Read base.html
        html_content = base_html_path.read_text()

        # Build URL to filename mapping and download assets
        url_to_filename: dict[str, str] = {}
        for asset in assets_to_vendor:
            url = asset["url"]
            filename = asset["filename"]
            url_to_filename[url] = filename
            filepath = vendor_dir / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            content = _download(url)
            filepath.write_bytes(content)

        # Generate LICENSES.txt from the JSON config
        _generate_licenses_file(vendor_dir, vendor_config["licenses"])

        # Rewrite HTML: replace CDN URLs with local paths and remove data-vendor attributes
        # Pattern matches: src="URL" ... data-vendor="filename" or href="URL" ... data-vendor="filename"
        vendor_pattern = re.compile(r'(src|href)="(https://[^"]+)"([^>]*?)data-vendor="([^"]+)"')

        def replace_vendor_tag(match: re.Match[str]) -> str:
            attr = match.group(1)  # src or href
            url = match.group(2)
            between = match.group(3)  # attributes between URL and data-vendor
            if url in url_to_filename:
                filename = url_to_filename[url]
                return f'{attr}="/static/vendor/{filename}"{between}'
            return match.group(0)

        modified_html = vendor_pattern.sub(replace_vendor_tag, html_content)

        # Inject vendored mode flag for JavaScript to detect
        # Insert right after <head> tag so it's available early
        modified_html = modified_html.replace(
            "<head>",
            "<head>\n    <script>window.CF_VENDORED=true;</script>",
            1,  # Only replace first occurrence
        )

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
