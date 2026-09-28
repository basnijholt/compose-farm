"""Tests for web UI authentication and origin checks."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from compose_farm.web.app import create_app
from compose_farm.web.auth import AuthSettings

LOCAL = {"base_url": "http://localhost", "client": ("127.0.0.1", 50000)}
REMOTE = {"base_url": "http://cf.example.com", "client": ("203.0.113.5", 50000)}
STATIC = "/static/app.js"


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CF_WEB_USERNAME", "CF_WEB_PASSWORD", "CF_WEB_NO_AUTH"):
        monkeypatch.delenv(var, raising=False)


class TestAuthSettings:
    """Loading settings from the environment."""

    def test_defaults(self) -> None:
        settings = AuthSettings.from_env()
        assert settings == AuthSettings(username="admin", password=None, no_auth=False)
        assert "localhost only" in settings.describe()

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_USERNAME", "bas")
        monkeypatch.setenv("CF_WEB_PASSWORD", "hunter2")
        monkeypatch.setenv("CF_WEB_NO_AUTH", "true")
        settings = AuthSettings.from_env()
        assert settings == AuthSettings(username="bas", password="hunter2", no_auth=True)  # noqa: S106
        # Password wins over no_auth
        assert "Basic auth" in settings.describe()

    def test_empty_password_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_PASSWORD", "")
        assert AuthSettings.from_env().password is None


class TestLocalhostOnlyDefault:
    """Without a password, only localhost may connect."""

    def test_localhost_allowed(self) -> None:
        assert TestClient(create_app(), **LOCAL).get(STATIC).status_code == 200

    def test_remote_client_blocked(self) -> None:
        response = TestClient(create_app(), **REMOTE).get(STATIC)
        assert response.status_code == 403
        assert "CF_WEB_PASSWORD" in response.text

    def test_dns_rebinding_blocked(self) -> None:
        """Loopback client with a foreign Host header (DNS rebinding) is blocked."""
        client = TestClient(create_app(), base_url="http://evil.example", client=("127.0.0.1", 1))
        assert client.get(STATIC).status_code == 403

    def test_api_blocked_for_remote(self) -> None:
        client = TestClient(create_app(), **REMOTE)
        assert client.get("/api/console/file?host=x&path=/etc/passwd").status_code == 403

    def test_websocket_blocked_for_remote(self) -> None:
        client = TestClient(create_app(), **REMOTE)
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/shell/local"):
            pass


class TestBasicAuth:
    """CF_WEB_PASSWORD enables HTTP Basic auth."""

    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("CF_WEB_PASSWORD", "s3cret")
        return TestClient(create_app(), **REMOTE)

    def test_missing_credentials(self, client: TestClient) -> None:
        response = client.get(STATIC)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic")

    @pytest.mark.parametrize(
        "headers",
        [
            _basic("admin", "wrong"),
            _basic("root", "s3cret"),
            {"Authorization": "Basic not-base64!"},
            {"Authorization": "Bearer s3cret"},
        ],
    )
    def test_bad_credentials(self, client: TestClient, headers: dict[str, str]) -> None:
        assert client.get(STATIC, headers=headers).status_code == 401

    def test_valid_credentials(self, client: TestClient) -> None:
        assert client.get(STATIC, headers=_basic("admin", "s3cret")).status_code == 200

    def test_localhost_still_needs_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_PASSWORD", "s3cret")
        assert TestClient(create_app(), **LOCAL).get(STATIC).status_code == 401

    def test_websocket_requires_credentials(self, client: TestClient) -> None:
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/shell/local"):
            pass

    def test_websocket_with_credentials(self, client: TestClient) -> None:
        with client.websocket_connect(
            "/ws/terminal/missing", headers=_basic("admin", "s3cret")
        ) as ws:
            assert "Task not found" in ws.receive_text()


class TestNoAuthOptOut:
    """CF_WEB_NO_AUTH disables the localhost restriction."""

    def test_remote_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_NO_AUTH", "1")
        assert TestClient(create_app(), **REMOTE).get(STATIC).status_code == 200


class TestOriginCheck:
    """Cross-origin state-changing requests and WebSockets are blocked."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(create_app(), **LOCAL)

    @pytest.mark.parametrize("origin", ["https://evil.example", "null"])
    def test_cross_origin_post_blocked(self, client: TestClient, origin: str) -> None:
        response = client.post("/api/stack/plex/down", headers={"Origin": origin})
        assert response.status_code == 403
        assert "Cross-origin" in response.text

    def test_cross_origin_get_allowed(self, client: TestClient) -> None:
        """Safe methods are not state-changing; browsers block reading the response."""
        response = client.get(STATIC, headers={"Origin": "https://evil.example"})
        assert response.status_code == 200

    def test_same_origin_post_passes(self, client: TestClient) -> None:
        response = client.post("/api/stack/plex/bogus", headers={"Origin": "http://localhost"})
        assert response.status_code == 404  # Reached the route

    def test_forwarded_host_post_passes(self, client: TestClient) -> None:
        response = client.post(
            "/api/stack/plex/bogus",
            headers={"Origin": "https://cf.example.com", "X-Forwarded-Host": "cf.example.com"},
        )
        assert response.status_code == 404

    def test_post_without_origin_passes(self, client: TestClient) -> None:
        assert client.post("/api/stack/plex/bogus").status_code == 404

    def test_cross_origin_websocket_blocked(self, client: TestClient) -> None:
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "ws://localhost/ws/terminal/missing", headers={"Origin": "https://evil.example"}
            ),
        ):
            pass

    def test_same_origin_websocket_allowed(self, client: TestClient) -> None:
        with client.websocket_connect(
            "ws://localhost/ws/terminal/missing", headers={"Origin": "http://localhost"}
        ) as ws:
            assert "Task not found" in ws.receive_text()
