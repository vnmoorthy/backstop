"""CORS allowlist + CSP / security-header middleware (stub).

Installs a CORS allowlist from :class:`Settings` (never a wildcard — closes
audit finding #6) plus a strict CSP, HSTS, ``X-Content-Type-Options``,
``X-Frame-Options=DENY`` and ``Referrer-Policy``. The same allowlist gates the
WebSocket ``Origin`` check at the edge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

    from backstop.infra.config import Settings


def install_security_middleware(app: FastAPI, settings: Settings) -> None:
    """Mount CORS allowlist + security-header middleware onto ``app``.

    Args:
        app: The FastAPI application to wrap.
        settings: The frozen application settings (provides the CORS allowlist).

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("install_security_middleware is implemented in WS-Integration")


def is_origin_allowed(origin: str, settings: Settings) -> bool:
    """Return whether ``origin`` is in the configured CORS allowlist.

    Shared by the HTTP CORS layer and the WebSocket ``Origin`` handshake check.

    Args:
        origin: The request ``Origin`` header value.
        settings: The frozen application settings.

    Returns:
        ``True`` if the origin is explicitly allowlisted.

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("is_origin_allowed is implemented in WS-Integration")


def content_security_policy(settings: Settings) -> str:
    """Build the strict Content-Security-Policy header value.

    Args:
        settings: The frozen application settings.

    Returns:
        The CSP header string.

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("content_security_policy is implemented in WS-Integration")


def allowed_origins(settings: Settings) -> List[str]:
    """Return the resolved CORS origin allowlist.

    Args:
        settings: The frozen application settings.

    Returns:
        The list of allowlisted origins (never containing ``*``).

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("allowed_origins is implemented in WS-Integration")
