"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _web_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable web UI auth so TestClient requests (from "testclient") reach routes.

    tests/web/test_auth.py clears this to test the auth modes.
    """
    monkeypatch.setenv("CF_WEB_NO_AUTH", "1")
    monkeypatch.delenv("CF_WEB_PASSWORD", raising=False)
