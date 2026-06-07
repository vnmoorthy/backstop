"""Stdlib HS256 access-token mint/verify shared by both transport adapters.

LiveKit join tokens are plain HS256 JSON Web Tokens whose payload carries a
``video`` grant object naming the room and the publish/subscribe rights. This
module reproduces that exact wire format using only the Python standard library
(``hmac`` + ``hashlib`` + ``base64`` + ``json``) so that:

* the *real* adapter and the *sim* adapter mint **identical** tokens and pass
  the same token contract, and
* no third-party JWT library is required at import or test time (the vendor
  ``livekit-api`` SDK is reserved for room *lifecycle* I/O, not crypto).

The grant shape follows the LiveKit ``AccessToken`` convention::

    {
      "iss": "<api_key>",          # issuer == the API key
      "sub": "<identity>",         # subject == participant identity
      "nbf": <issued-at epoch s>,
      "exp": <expiry epoch s>,
      "video": {                   # the room-scoped grant
        "room": "<room_name>",
        "roomJoin": true,
        "canPublish": <bool>,
        "canSubscribe": <bool>
      }
    }

Verification recomputes the HMAC in constant time and rejects tampered or
expired tokens by raising the domain :class:`Unauthenticated` error.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Dict, List

from backstop.domain.errors import Unauthenticated

__all__ = [
    "GrantClaims",
    "encode_token",
    "decode_token",
]

# JWT header for an HS256-signed token. Fixed bytes so encoding is deterministic.
_HEADER: Dict[str, str] = {"alg": "HS256", "typ": "JWT"}

# Allowed clock skew when checking ``nbf`` so a freshly minted token whose
# not-before lands a hair in the future is not spuriously rejected.
_LEEWAY = _dt.timedelta(seconds=30)


@dataclass(frozen=True)
class GrantClaims:
    """The decoded, verified claim set carried by a join token.

    Attributes:
        issuer: The ``iss`` claim (the transport API key).
        subject: The ``sub`` claim (the participant identity).
        room_name: The ``video.room`` grant the token is scoped to.
        not_before: The ``nbf`` instant (token validity start).
        expires_at: The ``exp`` instant (token validity end).
        can_publish: The ``video.canPublish`` grant.
        can_subscribe: The ``video.canSubscribe`` grant.
    """

    issuer: str
    subject: str
    room_name: str
    not_before: _dt.datetime
    expires_at: _dt.datetime
    can_publish: bool
    can_subscribe: bool


def _b64url_encode(raw: bytes) -> str:
    """Return the URL-safe, unpadded base64 of *raw* (JWT segment encoding)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    """Decode a URL-safe, unpadded base64 JWT *segment* back to bytes."""
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (ValueError, TypeError) as exc:  # malformed base64
        raise Unauthenticated("token segment is not valid base64url") from exc


def _sign(signing_input: bytes, secret: str) -> str:
    """Return the URL-safe base64 HMAC-SHA256 of *signing_input* under *secret*."""
    digest = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return _b64url_encode(digest)


def encode_token(
    *,
    api_key: str,
    secret: str,
    identity: str,
    room_name: str,
    issued_at: _dt.datetime,
    expires_at: _dt.datetime,
    can_publish: bool,
    can_subscribe: bool,
) -> str:
    """Mint a LiveKit-format HS256 join token (pure local crypto, no I/O).

    Args:
        api_key: The transport API key, emitted as the ``iss`` claim.
        secret: The HMAC signing secret (never leaves the process).
        identity: Participant identity, emitted as the ``sub`` claim.
        room_name: Room the grant is scoped to (``video.room``).
        issued_at: Token validity start, emitted as ``nbf``.
        expires_at: Token validity end, emitted as ``exp``.
        can_publish: Whether the grant permits publishing media.
        can_subscribe: Whether the grant permits subscribing to media.

    Returns:
        The encoded ``header.payload.signature`` JWT string.
    """
    payload: Dict[str, Any] = {
        "iss": api_key,
        "sub": identity,
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "video": {
            "room": room_name,
            "roomJoin": True,
            "canPublish": can_publish,
            "canSubscribe": can_subscribe,
        },
    }
    header_segment = _b64url_encode(
        json.dumps(_HEADER, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_segment = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _sign(signing_input, secret)
    return f"{header_segment}.{payload_segment}.{signature}"


def decode_token(token: str, *, secret: str, now: _dt.datetime) -> GrantClaims:
    """Verify *token* under *secret* and return its decoded grant claims.

    Args:
        token: The encoded ``header.payload.signature`` JWT.
        secret: The HMAC signing secret the token must verify under.
        now: The current instant, used for ``nbf``/``exp`` checks (injected so
            verification stays deterministic and clock-free).

    Returns:
        The verified :class:`GrantClaims`.

    Raises:
        Unauthenticated: If the token is malformed, tampered, signed with the
            wrong secret, not yet valid, or expired.
    """
    parts: List[str] = token.split(".")
    if len(parts) != 3:
        raise Unauthenticated("token is not a well-formed JWT")
    header_segment, payload_segment, signature = parts

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected = _sign(signing_input, secret)
    # Constant-time comparison defeats signature-timing oracles.
    if not hmac.compare_digest(expected, signature):
        raise Unauthenticated("token signature does not verify")

    header = _load_json(header_segment)
    if header.get("alg") != "HS256":
        raise Unauthenticated("unexpected token algorithm")

    payload = _load_json(payload_segment)
    grant = payload.get("video")
    if not isinstance(grant, dict):
        raise Unauthenticated("token carries no video grant")

    not_before = _epoch_to_dt(payload.get("nbf"))
    expires_at = _epoch_to_dt(payload.get("exp"))
    if now + _LEEWAY < not_before:
        raise Unauthenticated("token is not yet valid")
    if now >= expires_at:
        raise Unauthenticated("token has expired")

    issuer = payload.get("iss")
    subject = payload.get("sub")
    room_name = grant.get("room")
    if not (
        isinstance(issuer, str)
        and isinstance(subject, str)
        and isinstance(room_name, str)
    ):
        raise Unauthenticated("token is missing required claims")

    return GrantClaims(
        issuer=issuer,
        subject=subject,
        room_name=room_name,
        not_before=not_before,
        expires_at=expires_at,
        can_publish=bool(grant.get("canPublish", False)),
        can_subscribe=bool(grant.get("canSubscribe", False)),
    )


def _load_json(segment: str) -> Dict[str, Any]:
    """Decode a base64url JWT *segment* into a JSON object, or reject it."""
    try:
        loaded: Any = json.loads(_b64url_decode(segment).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Unauthenticated("token segment is not valid JSON") from exc
    if not isinstance(loaded, dict):
        raise Unauthenticated("token segment is not a JSON object")
    return loaded


def _epoch_to_dt(value: Any) -> _dt.datetime:
    """Convert a numeric epoch-seconds *value* to a UTC datetime, or reject it."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise Unauthenticated("token timestamp claim is not numeric")
    return _dt.datetime.fromtimestamp(float(value), tz=_dt.timezone.utc)
