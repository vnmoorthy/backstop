"""CORS allowlist + CSP / security-header middleware.

Installs a CORS allowlist from :class:`Settings` (never a wildcard — closes
audit finding #6) plus a strict CSP, ``X-Content-Type-Options``,
``X-Frame-Options=DENY`` and ``Referrer-Policy``. The same allowlist gates the
WebSocket ``Origin`` check at the edge via :func:`is_origin_allowed`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple

from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

    from backstop.infra.config import Settings

__all__ = [
    "SecurityHeadersMiddleware",
    "install_security_middleware",
    "is_origin_allowed",
    "content_security_policy",
    "allowed_origins",
]


def allowed_origins(settings: Settings) -> List[str]:
    """Return the resolved CORS origin allowlist (never containing ``*``)."""
    return [origin.strip() for origin in settings.cors_allow_origins if origin.strip()]


def is_origin_allowed(origin: str, settings: Settings) -> bool:
    """Return whether ``origin`` is in the configured CORS allowlist.

    Shared by the HTTP CORS layer and the WebSocket ``Origin`` handshake check.
    An empty/missing origin is never allowed.
    """
    if not origin:
        return False
    return origin in allowed_origins(settings)


def content_security_policy(settings: Settings) -> str:
    """Build the strict Content-Security-Policy header value.

    The dashboard ships a self-contained stylesheet/script (no CDN), so the
    policy is tight: same-origin documents, no framing, and connections only to
    self plus ``ws:``/``wss:`` for the live event stream.
    """
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware adding strict security headers to every response."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        """Store the wrapped app and precompute the static header set."""
        self._app = app
        self._headers: List[Tuple[bytes, bytes]] = [
            (b"content-security-policy", content_security_policy(settings).encode("latin-1")),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inject security headers on the HTTP response start message."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in self._headers:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self._app(scope, receive, send_with_headers)


def install_security_middleware(app: FastAPI, settings: Settings) -> None:
    """Mount CORS allowlist + security-header middleware onto ``app``.

    Args:
        app: The FastAPI application to wrap.
        settings: The frozen application settings (provides the CORS allowlist).
    """
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(settings),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
