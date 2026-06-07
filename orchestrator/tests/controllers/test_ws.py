"""WebSocket handshake: origin check + token handshake + channel authz.

The ``WS /v1/stream`` endpoint must reject a request from a disallowed origin,
a missing/invalid token, and an unauthorized channel — and accept a valid
origin + token for an allowed channel, then stream redacted events.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.event_bus_port import RedactedEvent
from tests.controllers.conftest import ALLOWED_ORIGIN, make_token

_WS_POLICY_VIOLATION = 1008


def test_ws_rejects_bad_origin(client: TestClient) -> None:
    """A request from an unlisted origin is closed with policy-violation 1008."""
    token = make_token(role="admin")
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(
        f"/v1/stream?token={token}&channel=system",
        headers={"Origin": "https://evil.example.com"},
    ):
        pass
    assert exc.value.code == _WS_POLICY_VIOLATION


def test_ws_rejects_missing_token(client: TestClient) -> None:
    """A request with no token is closed with 1008."""
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(
        "/v1/stream?channel=system",
        headers={"Origin": ALLOWED_ORIGIN},
    ):
        pass
    assert exc.value.code == _WS_POLICY_VIOLATION


def test_ws_rejects_invalid_token(client: TestClient) -> None:
    """A request with an unverifiable token is closed with 1008."""
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(
        "/v1/stream?token=not-a-jwt&channel=system",
        headers={"Origin": ALLOWED_ORIGIN},
    ):
        pass
    assert exc.value.code == _WS_POLICY_VIOLATION


def test_ws_rejects_unauthorized_channel(client: TestClient) -> None:
    """An agent may not subscribe to an appeal channel it does not own."""
    token = make_token(role="agent", owned=["ap-owned"])
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(
        "/v1/stream?token=" + token + "&channel=appeal:ap-not-owned",
        headers={"Origin": ALLOWED_ORIGIN},
    ):
        pass
    assert exc.value.code == _WS_POLICY_VIOLATION


def test_ws_accepts_valid_handshake_and_streams(client: TestClient) -> None:
    """A valid origin + token on a public channel is accepted and streams events."""
    token = make_token(role="admin")
    events = client.app.state.container.events

    with client.websocket_connect(
        f"/v1/stream?token={token}&channel=system",
        headers={"Origin": ALLOWED_ORIGIN},
    ) as ws:
        # Publish a redacted event into the channel from the server's event loop.
        body = RedactedText.from_redaction("swarm started", SANCTIONED_TOKEN)
        client.portal.call(
            events.publish,
            "system",
            RedactedEvent(kind="status", body=body, seq_iso="2026-06-07T12:00:00+00:00"),
        )
        message = ws.receive_json()
        assert message["kind"] == "status"
        assert message["body"] == "swarm started"
