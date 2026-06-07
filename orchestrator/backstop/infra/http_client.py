"""Shared async ``httpx`` client factory.

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

# A conservative shared-client timeout; individual adapters may pass a tighter
# per-request timeout (Moss does). Kept generous because the slowest path is the
# Unsiloed create-extract-then-poll round trip.
_DEFAULT_TIMEOUT_S: float = 30.0


def make_http_client(
    settings: Settings,
    *,
    base_url: Optional[str] = None,
) -> httpx.AsyncClient:
    """Create a shared async HTTP client (timeouts/TLS).

    Args:
        settings: The frozen application settings.
        base_url: Optional base URL to bind the client to. Adapters that issue
            relative paths (Moss, MiniMax) need a host-bound client; adapters
            that build absolute URLs (Unsiloed, Qwen) get the unbound shared
            client.

    Returns:
        A configured :class:`httpx.AsyncClient` to inject into real adapters.
    """
    import httpx

    timeout = httpx.Timeout(default_timeout_s(settings))
    if base_url is not None:
        return httpx.AsyncClient(base_url=base_url, timeout=timeout)
    return httpx.AsyncClient(timeout=timeout)


async def close_http_client(client: httpx.AsyncClient) -> None:
    """Gracefully close the shared async HTTP client on shutdown.

    Args:
        client: The client previously returned by :func:`make_http_client`.
    """
    await client.aclose()


def default_timeout_s(settings: Settings) -> Optional[float]:
    """Return the default per-request timeout, if configured.

    Args:
        settings: The frozen application settings.

    Returns:
        The default timeout in seconds.
    """
    return _DEFAULT_TIMEOUT_S
