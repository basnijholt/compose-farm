"""Tests for the browser-test CDN asset cache."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from compose_farm.web import cdn


def test_ensure_vendor_cache_redownloads_mismatched_cached_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached bytes that do not match the manifest digest must be replaced."""
    content = b"trusted asset"
    url = "https://cdn.example/asset.js"
    filepath = tmp_path / "asset.js"
    filepath.write_bytes(b"stale or corrupted")
    monkeypatch.setattr(
        cdn,
        "CDN_ASSETS",
        {
            url: (
                "asset.js",
                "application/javascript",
                hashlib.sha256(content).hexdigest(),
            )
        },
    )
    monkeypatch.setattr(cdn, "download_url", lambda _url: content)

    assert cdn.ensure_vendor_cache(tmp_path) == tmp_path
    assert filepath.read_bytes() == content


def test_ensure_vendor_cache_rejects_mismatched_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unverified replacement bytes must not overwrite the cache."""
    url = "https://cdn.example/asset.js"
    filepath = tmp_path / "asset.js"
    filepath.write_bytes(b"existing corrupted asset")
    monkeypatch.setattr(
        cdn,
        "CDN_ASSETS",
        {
            url: (
                "asset.js",
                "application/javascript",
                hashlib.sha256(b"trusted asset").hexdigest(),
            )
        },
    )
    monkeypatch.setattr(cdn, "download_url", lambda _url: b"changed download")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        cdn.ensure_vendor_cache(tmp_path)

    assert filepath.read_bytes() == b"existing corrupted asset"
