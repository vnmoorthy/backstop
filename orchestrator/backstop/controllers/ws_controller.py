"""WebSocket controller: origin-checked, token-authenticated event stream.

``WS /v1/stream`` is the live redacted-event egress to the dashboard. The
handshake enforces, in order:

1. **Origin check** — the ``Origin`` header must be in the CORS allowlist, else
   the socket is closed with policy-violation ``1008`` (closes the no-WS-origin
   finding).
2. **Token handshake** — a bearer token (``?token=`` query param) is verified
   through the :class:`AuthPort`; an invalid token closes with ``1008``.
3. **Channel authz** — the event bus authorises the principal for the requested
   channel (per-appeal ownership) before any event is delivered.

Only :class:`RedactedText`-typed event bodies ever reach the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Query, WebSocket
from starlette.websockets import WebSocketDisconnect

from backstop.domain.errors import Forbidden, Unauthenticated
from backstop.infra.security_headers import is_origin_allowed

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.composition.container import Container

router = APIRouter(tags=["ws"])

# Policy-violation close code (per RFC 6455) used for every handshake rejection.
_WS_POLICY_VIOLATION = 1008


@router.websocket("/stream")
async def stream(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
    channel: str = Query(default="system"),
) -> None:
    """Stream redacted events after an origin + token + channel handshake."""
    container: Container = websocket.app.state.container
    settings = container.settings
    auth = container.auth_service
    events = container.events
    assert settings is not None and auth is not None and events is not None  # noqa: S101

    # 1. Origin check — reject before accepting the socket.
    origin = websocket.headers.get("origin", "")
    if not is_origin_allowed(origin, settings):
        await websocket.close(code=_WS_POLICY_VIOLATION, reason="origin not allowed")
        return

    # 2. Token handshake.
    if not token:
        await websocket.close(code=_WS_POLICY_VIOLATION, reason="missing token")
        return
    try:
        principal = auth.authenticate(token)
    except Unauthenticated:
        await websocket.close(code=_WS_POLICY_VIOLATION, reason="invalid token")
        return

    # 3. Channel authorization (per-appeal ownership) before accepting.
    try:
        stream_iter = events.subscribe(channel, principal)
    except Forbidden:
        await websocket.close(code=_WS_POLICY_VIOLATION, reason="forbidden channel")
        return

    await websocket.accept()
    try:
        async for event in stream_iter:
            await websocket.send_json(
                {
                    "kind": event.kind,
                    "body": str(event.body),
                    "seq_iso": event.seq_iso,
                }
            )
    except WebSocketDisconnect:
        return
