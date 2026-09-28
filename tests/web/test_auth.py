"""Tests for web UI authentication and origin checks."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from compose_farm.web.app import create_app
from compose_farm.web.auth import AuthSettings

LOCAL: dict[str, Any] = {"base_url": "http://localhost", "client": ("127.0.0.1", 50000)}
REMOTE: dict[str, Any] = {"base_url": "http://cf.example.com", "client": ("203.0.113.5", 50000)}
UNKNOWN: dict[str, Any] = {"base_url": "http://localhost", "client": ("testclient", 50000)}
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
        assert "CF_WEB_PASSWORD" in settings.describe()

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_USERNAME", "bas")
        monkeypatch.setenv("CF_WEB_PASSWORD", "hunter2")
        settings = AuthSettings.from_env()
        assert settings == AuthSettings(username="bas", password="hunter2")  # noqa: S106
        assert "Basic auth" in settings.describe()

    def test_empty_password_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_PASSWORD", "")
        assert AuthSettings.from_env().password is None

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_explicit_no_auth_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("CF_WEB_NO_AUTH", value)
        assert AuthSettings.from_env().no_auth is True


class TestNoPassword:
    """Without CF_WEB_PASSWORD, only loopback clients are allowed."""

    def test_remote_blocked(self) -> None:
        response = TestClient(create_app(), **REMOTE).get(STATIC)
        assert response.status_code == 403
        assert "CF_WEB_PASSWORD" in response.text

    def test_local_allowed(self) -> None:
        assert TestClient(create_app(), **LOCAL).get(STATIC).status_code == 200

    def test_loopback_peer_with_remote_host_is_blocked(self) -> None:
        """A DNS-rebinding hostname must not inherit loopback-only access."""
        client = TestClient(
            create_app(), base_url="http://attacker.example", client=("127.0.0.1", 50000)
        )
        response = client.post(
            "/api/stack/plex/down", headers={"Origin": "http://attacker.example"}
        )
        assert response.status_code == 403
        assert "CF_WEB_PASSWORD" in response.text

    def test_unrecognized_peer_is_blocked(self) -> None:
        assert TestClient(create_app(), **UNKNOWN).get(STATIC).status_code == 403

    def test_remote_websocket_blocked(self) -> None:
        client = TestClient(create_app(), **REMOTE)
        with (
            pytest.raises(WebSocketDenialResponse) as exc_info,
            client.websocket_connect("ws://cf.example.com/ws/terminal/missing"),
        ):
            pass
        assert exc_info.value.status_code == 403

    def test_explicit_no_auth_allows_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_NO_AUTH", "1")
        assert TestClient(create_app(), **REMOTE).get(STATIC).status_code == 200


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
        """Handshake gets a real 401 challenge so browsers retry with saved credentials."""
        with (
            pytest.raises(WebSocketDenialResponse) as exc_info,
            client.websocket_connect("/ws/shell/local"),
        ):
            pass
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["www-authenticate"].startswith("Basic")

    def test_websocket_with_credentials(self, client: TestClient) -> None:
        with client.websocket_connect(
            "/ws/terminal/missing", headers=_basic("admin", "s3cret")
        ) as ws:
            assert "Task not found" in ws.receive_text()


class TestOriginCheck:
    """Cross-origin state-changing requests and WebSockets are blocked."""

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(create_app(), **LOCAL)

    @pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://["])
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

    def test_http_origin_to_https_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CF_WEB_NO_AUTH", "1")
        client = TestClient(
            create_app(), base_url="https://cf.example.com", client=("127.0.0.1", 50000)
        )
        response = client.post("/api/stack/plex/bogus", headers={"Origin": "http://cf.example.com"})
        assert response.status_code == 403

    def test_http_origin_to_forwarded_https_blocked(self, client: TestClient) -> None:
        response = client.post(
            "/api/stack/plex/bogus",
            headers={"Origin": "http://localhost", "X-Forwarded-Proto": "https"},
        )
        assert response.status_code == 403

    def test_https_origin_behind_plain_http_proxy_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TLS-terminating proxy without X-Forwarded-Proto: app sees http, Origin is https."""
        monkeypatch.setenv("CF_WEB_NO_AUTH", "1")
        client = TestClient(
            create_app(), base_url="http://cf.example.com", client=("127.0.0.1", 50000)
        )
        response = client.post(
            "/api/stack/plex/bogus", headers={"Origin": "https://cf.example.com"}
        )
        assert response.status_code == 404

    def test_default_port_in_host_passes(self, client: TestClient) -> None:
        """Proxies may send "Host: example.com:443" while the browser Origin omits it."""
        response = client.post(
            "/api/stack/plex/bogus",
            headers={"Origin": "https://cf.example.com", "X-Forwarded-Host": "cf.example.com:443"},
        )
        assert response.status_code == 404

    def test_non_default_port_mismatch_blocked(self, client: TestClient) -> None:
        response = client.post(
            "/api/stack/plex/bogus",
            headers={"Origin": "https://cf.example.com", "X-Forwarded-Host": "cf.example.com:8443"},
        )
        assert response.status_code == 403

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
