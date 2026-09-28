"""Tests for web action routes and CLI argument handling (injection safety)."""

from __future__ import annotations

import asyncio
import shlex
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from compose_farm.web import streaming
from compose_farm.web.routes import actions

if TYPE_CHECKING:
    from compose_farm.config import Config

MALICIOUS_SERVICE = "x; touch /tmp/pwned"


@pytest.fixture
def client(mock_config: Config, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client with get_config patched in the actions module."""
    from compose_farm.web.app import create_app

    monkeypatch.setattr(actions, "get_config", lambda: mock_config)
    return TestClient(create_app())


class TestServiceAction:
    """Tests for POST /api/stack/{name}/service/{service}/{command}."""

    def test_unknown_service_returns_404(self, client: TestClient) -> None:
        """Services not defined in the stack's compose file are rejected."""
        with patch.object(actions, "_start_task") as mock_start:
            response = client.post("/api/stack/plex/service/x;id/restart")

        assert response.status_code == 404
        assert response.json()["detail"] == "Service 'x;id' not found in 'plex'"
        mock_start.assert_not_called()

    def test_known_service_passes_single_argv_item(self, client: TestClient) -> None:
        """Valid services are passed as one --service=<name> argv item."""
        with patch.object(actions, "run_compose_streaming", new=AsyncMock()) as mock_stream:
            response = client.post("/api/stack/plex/service/plex/restart")

        assert response.status_code == 200
        assert response.json()["service"] == "plex"
        mock_stream.assert_awaited_once()
        args = mock_stream.await_args.args
        assert args[1:3] == ("plex", "restart")
        assert args[4] == ["--service=plex"]


class TestRunComposeStreaming:
    """Tests for building cf CLI argv in the streaming adapter."""

    def test_extra_args_are_not_split(self, mock_config: Config) -> None:
        """Extra args (e.g. a service name with spaces) stay a single argv item."""
        with patch.object(streaming, "run_cli_streaming", new=AsyncMock()) as mock_cli:
            asyncio.run(
                streaming.run_compose_streaming(
                    mock_config, "plex", "restart", "tid", [f"--service={MALICIOUS_SERVICE}"]
                )
            )

        mock_cli.assert_awaited_once_with(
            mock_config, ["restart", "plex", f"--service={MALICIOUS_SERVICE}"], "tid"
        )

    def test_self_update_via_ssh_quotes_args(
        self, mock_config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Self-update builds a correctly quoted nested sh -c command."""
        monkeypatch.setenv("CF_WEB_STACK", "plex")
        streaming.tasks["tid"] = {"status": "running", "output": []}
        try:
            with (
                patch.object(streaming, "build_ssh_command", return_value=["ssh"]) as mock_ssh,
                patch.object(streaming, "_stream_subprocess", new=AsyncMock(return_value=0)),
            ):
                asyncio.run(
                    streaming.run_compose_streaming(
                        mock_config, "plex", "update", "tid", [f"--service={MALICIOUS_SERVICE}"]
                    )
                )
        finally:
            streaming.tasks.pop("tid", None)

        remote_cmd = mock_ssh.call_args.args[1]
        outer = shlex.split(remote_cmd)
        inner = shlex.split(outer[outer.index("-c") + 1])
        assert inner[:4] == ["cf", "update", "plex", f"--service={MALICIOUS_SERVICE}"]
        assert inner[4] == f"--config={mock_config.config_path}"
        assert inner[5:7] == [">", "/tmp/cf-self-update-tid.log"]
