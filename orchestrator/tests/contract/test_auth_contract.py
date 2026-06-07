"""Contract suite for :class:`AuthPort` (JWT verify + RBAC + ownership).

The concrete adapter is asserted to honour the port. Load-bearing M13
assertions:

* a missing / malformed / wrong-issuer / expired / bad-signature token raises the
  domain :class:`Unauthenticated` -- never a raw PyJWT exception;
* RBAC: a role without the grant for ``(action, resource)`` is refused;
* per-appeal ownership: a principal may not act on an appeal it does not own
  (cross-appeal access is :class:`Forbidden`).

Tokens are minted with real PyJWT; the adapter imports the SDK lazily, so a
missing SDK never blocks the module import (only token minting in this test
needs it, and PyJWT is a core dependency).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import jwt
import pytest

from backstop.adapters.auth.jwt_auth_adapter import JwtAuthAdapter
from backstop.domain.errors import Forbidden, Unauthenticated
from backstop.ports.auth_port import AuthorizationRequest, AuthPort, Principal

# A >= 32-byte HMAC secret so PyJWT does not emit an InsecureKeyLengthWarning
# (the suite runs with ``filterwarnings = ["error"]``).
_SECRET = "test-secret-key-that-is-long-enough-for-hs256"
_ISSUER = "backstop"


def _adapter() -> JwtAuthAdapter:
    return JwtAuthAdapter(secret=_SECRET, issuer=_ISSUER)


def _token(
    *,
    subject: str = "nurse-1",
    role: str = "nurse",
    owned: Optional[List[str]] = None,
    issuer: str = _ISSUER,
    secret: str = _SECRET,
    expires_in: int = 3600,
    drop_exp: bool = False,
) -> str:
    """Mint a signed JWT for the test."""
    now = dt.datetime.now(tz=dt.timezone.utc)
    claims: Dict[str, Any] = {
        "sub": subject,
        "role": role,
        "owned": owned if owned is not None else [],
        "iss": issuer,
    }
    if not drop_exp:
        claims["exp"] = now + dt.timedelta(seconds=expires_in)
    return jwt.encode(claims, secret, algorithm="HS256")


def test_adapter_satisfies_the_port() -> None:
    """The concrete adapter is recognised as the runtime-checkable port."""
    assert isinstance(_adapter(), AuthPort)


def test_authenticate_returns_principal() -> None:
    """A valid token yields a populated :class:`Principal`."""
    adapter = _adapter()
    principal = adapter.authenticate(_token(subject="nurse-1", role="nurse", owned=["ap-1"]))
    assert isinstance(principal, Principal)
    assert principal.subject == "nurse-1"
    assert principal.role == "nurse"
    assert principal.owned_ids == frozenset({"ap-1"})


def test_authenticate_accepts_bearer_prefix() -> None:
    """An ``Authorization: Bearer <jwt>`` header value is accepted."""
    adapter = _adapter()
    principal = adapter.authenticate("Bearer " + _token())
    assert principal.subject == "nurse-1"


@pytest.mark.parametrize("token", ["", "   ", "not-a-jwt", "Bearer ", "Bearer not.a.jwt"])
def test_authenticate_rejects_missing_or_malformed(token: str) -> None:
    """Empty / malformed tokens raise the domain auth error, not a PyJWT one."""
    with pytest.raises(Unauthenticated):
        _adapter().authenticate(token)


def test_authenticate_rejects_bad_signature() -> None:
    """A token signed with the wrong secret is rejected."""
    forged = _token(secret="attacker-secret-also-long-enough-for-hs256-hmac")
    with pytest.raises(Unauthenticated):
        _adapter().authenticate(forged)


def test_authenticate_rejects_wrong_issuer() -> None:
    """A token from an unexpected issuer is rejected."""
    bad = _token(issuer="evil-co")
    with pytest.raises(Unauthenticated):
        _adapter().authenticate(bad)


def test_authenticate_rejects_expired() -> None:
    """An expired token is rejected (no PyJWT exception escapes)."""
    expired = _token(expires_in=-10)
    with pytest.raises(Unauthenticated):
        _adapter().authenticate(expired)


def test_authenticate_rejects_missing_exp() -> None:
    """A token without the required ``exp`` claim is rejected."""
    with pytest.raises(Unauthenticated):
        _adapter().authenticate(_token(drop_exp=True))


def test_authorize_allows_owned_appeal() -> None:
    """A nurse may read an appeal it owns."""
    adapter = _adapter()
    principal = adapter.authenticate(_token(role="nurse", owned=["ap-1"]))
    adapter.authorize(
        principal, AuthorizationRequest(action="read", resource="appeals", resource_id="ap-1")
    )  # no raise


def test_authorize_refuses_cross_appeal_access() -> None:
    """A nurse may NOT read an appeal it does not own (cross-appeal access)."""
    adapter = _adapter()
    principal = adapter.authenticate(_token(role="nurse", owned=["ap-1"]))
    with pytest.raises(Forbidden):
        adapter.authorize(
            principal,
            AuthorizationRequest(action="read", resource="appeals", resource_id="ap-999"),
        )


def test_authorize_enforces_rbac_grant() -> None:
    """A role without the ``(action, resource)`` grant is refused."""
    adapter = _adapter()
    # A nurse cannot create appeals (only agents/admins can).
    nurse = adapter.authenticate(_token(role="nurse", owned=["ap-1"]))
    with pytest.raises(Forbidden):
        adapter.authorize(
            nurse,
            AuthorizationRequest(action="create", resource="appeals", resource_id="ap-1"),
        )


def test_authorize_admin_bypasses_ownership() -> None:
    """An admin may act across appeals it does not personally own (break-glass)."""
    adapter = _adapter()
    admin = adapter.authenticate(_token(subject="root", role="admin", owned=[]))
    adapter.authorize(
        admin,
        AuthorizationRequest(action="read", resource="appeals", resource_id="ap-anything"),
    )  # no raise


def test_unknown_role_is_powerless() -> None:
    """An unknown role holds no grants at all."""
    adapter = _adapter()
    principal = adapter.authenticate(_token(role="intruder", owned=["ap-1"]))
    with pytest.raises(Forbidden):
        adapter.authorize(
            principal,
            AuthorizationRequest(action="read", resource="appeals", resource_id="ap-1"),
        )


def test_sign_action_requires_ownership() -> None:
    """The sign-off action is gated by ownership for a nurse."""
    adapter = _adapter()
    nurse = adapter.authenticate(_token(role="nurse", owned=["ap-1"]))
    # Owned -> allowed.
    adapter.authorize(
        nurse, AuthorizationRequest(action="sign", resource="appeals", resource_id="ap-1")
    )
    # Not owned -> forbidden.
    with pytest.raises(Forbidden):
        adapter.authorize(
            nurse, AuthorizationRequest(action="sign", resource="appeals", resource_id="ap-2")
        )
