"""Authentication and origin checks for the web UI.

The web UI can open shells on hosts, so every HTTP and WebSocket request is checked:

- State-changing requests and WebSocket handshakes from browsers must come from the
  same origin. This blocks websites you visit from driving the UI through your browser
  (CSRF and cross-site WebSocket hijacking). Always on, no configuration needed.
- Without a password, only requests with both a loopback peer and loopback Host are
  accepted. ``CF_WEB_NO_AUTH=1`` explicitly permits remote passwordless access behind
  a trusted access layer.
- If ``CF_WEB_PASSWORD`` is set, HTTP Basic auth is required (username
  ``CF_WEB_USERNAME``, default ``admin``).
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DEFAULT_PORTS = {"http": 80, "https": 443}
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class AuthSettings:
    """Web UI authentication settings."""

    username: str = "admin"
    password: str | None = None
    no_auth: bool = False

    @classmethod
    def from_env(cls) -> AuthSettings:
        """Load settings from the web authentication environment variables."""
        return cls(
            username=os.environ.get("CF_WEB_USERNAME") or "admin",
            password=os.environ.get("CF_WEB_PASSWORD") or None,
            no_auth=os.environ.get("CF_WEB_NO_AUTH", "").lower() in TRUE_VALUES,
        )

    def describe(self) -> str:
        """Human-readable summary of the active auth mode."""
        if self.password:
            return f"HTTP Basic auth (user '{self.username}')"
        if self.no_auth:
            return "none (remote passwordless access explicitly enabled)"
        return "loopback only (set CF_WEB_PASSWORD for remote access)"


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


def _first(value: str) -> str:
    """First entry of a possibly comma-separated proxy header, lowercased."""
    return value.split(",", maxsplit=1)[0].strip().lower()


def _is_loopback_client(scope: Scope) -> bool:
    """Return whether the ASGI peer is a loopback address."""
    client = scope.get("client")
    if not client:
        return False
    host = client[0]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _is_loopback_host(headers: dict[str, str]) -> bool:
    """Return whether Host names localhost or a loopback IP address."""
    try:
        hostname = urlsplit(f"//{headers.get('host', '')}").hostname
    except ValueError:
        return False
    if hostname is None:
        return False
    if hostname.rstrip(".").lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _is_same_origin(scope: Scope, headers: dict[str, str]) -> bool:
    """Check Origin matches Host (or X-Forwarded-Host). Requests without Origin pass."""
    origin = headers.get("origin")
    if origin is None:
        return True  # Non-browser client; CSRF needs a browser
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    origin_netloc = parsed.netloc.lower()
    if not origin_netloc:
        return False  # e.g. "null" from sandboxed iframes
    # An http:// page must not drive an https:// UI (e.g. content injected over plain HTTP).
    # The reverse is allowed: TLS-terminating proxies often omit X-Forwarded-Proto.
    request_scheme = _first(headers.get("x-forwarded-proto", "")) or scope["scheme"]
    if parsed.scheme == "http" and request_scheme in ("https", "wss"):
        return False
    allowed = {headers.get("host", "").lower()}
    if forwarded := headers.get("x-forwarded-host"):
        allowed.add(_first(forwarded))
    # Browsers omit default ports in Origin, but proxies may send e.g. "Host: example.com:443"
    default_port = DEFAULT_PORTS.get(parsed.scheme)
    allowed |= {host.removesuffix(f":{default_port}") for host in allowed}
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

        if (
            self.settings.password is None
            and not self.settings.no_auth
            and not (_is_loopback_client(scope) and _is_loopback_host(headers))
        ):
            await self._reject(
                scope,
                send,
                403,
                "Remote access requires CF_WEB_PASSWORD (log in as CF_WEB_USERNAME, default admin). "
                "Set CF_WEB_NO_AUTH=1 only behind a trusted access layer.\n"
                "With Docker, set them in .env; docker-compose.yml files from before v1.22.1 "
                "don't pass them to the container.\n",
            )
            return

        if (is_ws or scope["method"] not in SAFE_METHODS) and not _is_same_origin(scope, headers):
            await self._reject(scope, send, 403, "Cross-origin request blocked.\n")
            return

        if self.settings.password and not _check_basic_auth(
            headers.get("authorization", ""), self.settings
        ):
            await self._reject(scope, send, 401, "Authentication required.\n")
            return

        await self.app(scope, receive, send)

    async def _reject(self, scope: Scope, send: Send, status: int, message: str) -> None:
        response_headers = [(b"content-type", b"text/plain; charset=utf-8")]
        if status == 401:  # noqa: PLR2004
            # Browsers only retry with saved credentials after this challenge, which
            # matters for WebSocket paths outside the one the user logged in on
            response_headers.append((b"www-authenticate", b'Basic realm="Compose Farm"'))
        prefix = "http"
        if scope["type"] == "websocket":
            if "websocket.http.response" not in scope.get("extensions", {}):
                # Closing before accept makes the server answer the handshake with HTTP 403
                await send({"type": "websocket.close", "code": 1008})
                return
            prefix = "websocket.http"
        start = {"type": f"{prefix}.response.start", "status": status, "headers": response_headers}
        await send(start)
        await send({"type": f"{prefix}.response.body", "body": message.encode()})
