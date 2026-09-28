"""Tests for build-time vendor asset integrity checks."""

from __future__ import annotations

import hashlib

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

    with pytest.raises(ValueError, match="SHA-256 mismatch.*cdn.example/asset.js"):
        hatch_build._verify_download(asset, b"tampered asset")


def test_verify_download_requires_a_digest() -> None:
    """Every remotely downloaded build input must declare a digest."""
    with pytest.raises(ValueError, match="Missing SHA-256"):
        hatch_build._verify_download({"url": "https://cdn.example/asset.js"}, b"asset")
