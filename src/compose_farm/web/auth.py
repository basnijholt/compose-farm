"""Authentication and origin checks for the web UI.

The web UI can open shells on hosts, so every HTTP and WebSocket request is checked:

- State-changing requests and WebSocket handshakes from browsers must come from the
  same origin. This blocks websites you visit from driving the UI through your browser
  (CSRF and cross-site WebSocket hijacking). Always on, no configuration needed.
- If ``CF_WEB_PASSWORD`` is set, HTTP Basic auth is required (username
  ``CF_WEB_USERNAME``, default ``admin``).
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class AuthSettings:
    """Web UI authentication settings."""

    username: str = "admin"
    password: str | None = None

    @classmethod
    def from_env(cls) -> AuthSettings:
        """Load settings from CF_WEB_USERNAME and CF_WEB_PASSWORD."""
        return cls(
            username=os.environ.get("CF_WEB_USERNAME") or "admin",
            password=os.environ.get("CF_WEB_PASSWORD") or None,
        )

    def describe(self) -> str:
        """Human-readable summary of the active auth mode."""
        if self.password:
            return f"HTTP Basic auth (user '{self.username}')"
        return "none (set CF_WEB_PASSWORD to require a login)"


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}


def _check_basic_auth(header: str, settings: AuthSettings) -> bool:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or settings.password is None:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    # Compare both to avoid leaking which one was wrong via timing
    user_ok = secrets.compare_digest(username.encode(), settings.username.encode())
    pass_ok = secrets.compare_digest(password.encode(), settings.password.encode())
    return user_ok and pass_ok


def _is_same_origin(headers: dict[str, str]) -> bool:
    """Check Origin matches Host (or X-Forwarded-Host). Requests without Origin pass."""
    origin = headers.get("origin")
    if origin is None:
        return True  # Non-browser client; CSRF needs a browser
    origin_netloc = urlsplit(origin).netloc.lower()
    if not origin_netloc:
        return False  # e.g. "null" from sandboxed iframes
    allowed = {headers.get("host", "").lower()}
    if forwarded := headers.get("x-forwarded-host"):
        allowed.add(forwarded.split(",")[0].strip().lower())
    return origin_netloc in allowed


class AuthMiddleware:
    """ASGI middleware enforcing same-origin checks and optional auth on HTTP and WebSocket."""

    def __init__(self, app: ASGIApp, settings: AuthSettings) -> None:
        """Wrap an ASGI app with the given auth settings."""
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject unauthenticated or cross-origin requests before they reach routes."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        is_ws = scope["type"] == "websocket"

        if (is_ws or scope["method"] not in SAFE_METHODS) and not _is_same_origin(headers):
            await self._reject(scope, send, 403, "Cross-origin request blocked.\n")
            return

        if self.settings.password and not _check_basic_auth(
            headers.get("authorization", ""), self.settings
        ):
            await self._reject(scope, send, 401, "Authentication required.\n")
            return

        await self.app(scope, receive, send)

    async def _reject(self, scope: Scope, send: Send, status: int, message: str) -> None:
        if scope["type"] == "websocket":
            # Closing before accept makes the server answer the handshake with HTTP 403
            await send({"type": "websocket.close", "code": 1008})
            return
        response_headers = [(b"content-type", b"text/plain; charset=utf-8")]
        if status == 401:  # noqa: PLR2004
            response_headers.append((b"www-authenticate", b'Basic realm="Compose Farm"'))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": message.encode()})
