"""Tests for build-time vendor asset integrity checks."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import hatch_build


def test_verify_download_returns_matching_content() -> None:
    """A pinned asset with the expected digest is accepted unchanged."""
    content = b"trusted asset"
    asset = {
        "url": "https://cdn.example/asset.js",
        "sha256": hashlib.sha256(content).hexdigest(),
    }

    assert hatch_build._verify_download(asset, content) == content


def test_verify_download_rejects_changed_content() -> None:
    """A CDN response that differs from the reviewed bytes is rejected."""
    asset = {"url": "https://cdn.example/asset.js", "sha256": "0" * 64}

    with pytest.raises(ValueError, match=r"SHA-256 mismatch.*cdn\.example/asset\.js"):
        hatch_build._verify_download(asset, b"tampered asset")


def test_verify_download_requires_a_digest() -> None:
    """Every remotely downloaded build input must declare a digest."""
    with pytest.raises(ValueError, match="Missing SHA-256"):
        hatch_build._verify_download({"url": "https://cdn.example/asset.js"}, b"asset")


def test_initialize_cleans_temp_dir_after_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected download must not leave build inputs in the temporary directory."""
    template = tmp_path / "src/compose_farm/web/templates/base.html"
    template.parent.mkdir(parents=True)
    template.write_text('<head><script src="https://cdn.example/asset.js"></script>')
    temp_dir = tmp_path / "vendor-temp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_kwargs: str(temp_dir))
    monkeypatch.setattr(
        hatch_build,
        "_load_vendor_assets",
        lambda _root: {
            "assets": [
                {
                    "url": "https://cdn.example/asset.js",
                    "filename": "asset.js",
                    "sha256": "0" * 64,
                }
            ],
            "licenses": {},
        },
    )
    monkeypatch.setattr(hatch_build, "_download", lambda _url: b"tampered")
    hook = hatch_build.VendorAssetsHook(str(tmp_path), {}, MagicMock(), MagicMock(), "", "wheel")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        hook.initialize("1.0", {})

    assert not temp_dir.exists()
