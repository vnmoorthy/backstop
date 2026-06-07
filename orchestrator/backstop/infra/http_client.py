"""Shared async ``httpx`` client factory (stub).

The sole place — alongside the real adapters and the PAVO adapters — permitted to
import ``httpx``. Real HTTP adapters (Moss, TrueFoundry, Unsiloed, MiniMax, Qwen)
receive an injected :class:`httpx.AsyncClient` so they never construct their own
transport, and the client is closed once on application shutdown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx

    from backstop.infra.config import Settings


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    """Create the shared async HTTP client (timeouts/retries/TLS).

    Args:
        settings: The frozen application settings.

    Returns:
        A configured :class:`httpx.AsyncClient` to inject into real adapters.

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("make_http_client is implemented in WS-Integration")


async def close_http_client(client: httpx.AsyncClient) -> None:
    """Gracefully close the shared async HTTP client on shutdown.

    Args:
        client: The client previously returned by :func:`make_http_client`.

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("close_http_client is implemented in WS-Integration")


def default_timeout_s(settings: Settings) -> Optional[float]:
    """Return the default per-request timeout, if configured.

    Args:
        settings: The frozen application settings.

    Returns:
        The default timeout in seconds, or ``None`` for the library default.

    Raises:
        NotImplementedError: Always — filled in by WS-Integration.
    """
    raise NotImplementedError("default_timeout_s is implemented in WS-Integration")
