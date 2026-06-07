"""JWT/HMAC authentication + RBAC authorization adapter for :class:`AuthPort`.

Implements :class:`backstop.ports.auth_port.AuthPort`. ``authenticate`` verifies
a signed (HS256) JWT bearer token -- signature, issuer, and expiry -- and turns
it into a :class:`~backstop.ports.auth_port.Principal`; ``authorize`` gates an
``(action, resource, resource_id)`` request against the principal's RBAC role
and owned ids (per-appeal ownership). Every vendor/JWT failure is translated
into a domain error -- :class:`~backstop.domain.errors.Unauthenticated` or
:class:`~backstop.domain.errors.Forbidden` -- so a raw ``jwt`` exception never
escapes the port.

The ``PyJWT`` library is imported lazily inside :meth:`authenticate` so this
module imports cleanly even when the SDK is absent.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Mapping, Tuple

from backstop.domain.errors import Forbidden, Unauthenticated
from backstop.ports.auth_port import AuthorizationRequest, Principal

__all__ = ["JwtAuthAdapter"]

# RBAC matrix: role -> set of (action, resource) verbs the role may perform at
# all. Ownership of a specific ``resource_id`` is enforced separately below so a
# role grant never bypasses per-appeal ownership.
_ROLE_GRANTS: Mapping[str, FrozenSet[Tuple[str, str]]] = {
    "admin": frozenset(
        {
            ("create", "appeals"),
            ("read", "appeals"),
            ("update", "appeals"),
            ("sign", "appeals"),
            ("read", "files"),
            ("read", "events"),
        }
    ),
    "nurse": frozenset(
        {
            ("read", "appeals"),
            ("sign", "appeals"),
            ("read", "files"),
            ("read", "events"),
        }
    ),
    "agent": frozenset(
        {
            ("create", "appeals"),
            ("read", "appeals"),
            ("update", "appeals"),
            ("read", "files"),
            ("read", "events"),
        }
    ),
}

# Verbs that operate on a specific instance and therefore require the principal
# to own ``resource_id`` (in addition to holding the role grant).
_OWNERSHIP_REQUIRED: FrozenSet[Tuple[str, str]] = frozenset(
    {
        ("read", "appeals"),
        ("update", "appeals"),
        ("sign", "appeals"),
        ("read", "files"),
        ("read", "events"),
    }
)

# Admins may act across appeals they do not personally own (break-glass / audit).
_OWNERSHIP_EXEMPT_ROLES: FrozenSet[str] = frozenset({"admin"})


class JwtAuthAdapter:
    """HS256 JWT verify + RBAC :class:`~backstop.ports.auth_port.AuthPort`.

    Construction injects the shared signing ``secret`` and expected ``issuer``
    (both from frozen settings). ``leeway_seconds`` tolerates minor clock skew on
    the ``exp`` claim.
    """

    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        algorithm: str = "HS256",
        leeway_seconds: int = 0,
    ) -> None:
        """Store the verification material; no key material is logged."""
        self._secret = secret
        self._issuer = issuer
        self._algorithm = algorithm
        self._leeway = leeway_seconds

    def authenticate(self, token: str) -> Principal:
        """Verify ``token`` and return the authenticated :class:`Principal`.

        Strips an optional ``Bearer `` prefix, then verifies signature, issuer
        and expiry. Raises :class:`Unauthenticated` for a missing, malformed,
        expired or badly-signed token; never raises a vendor/JWT exception.
        """
        import jwt
        from jwt import InvalidTokenError

        raw = token.strip()
        if not raw:
            raise Unauthenticated("missing bearer token")
        if raw.lower().startswith("bearer "):
            raw = raw[len("bearer ") :].strip()
        if not raw:
            raise Unauthenticated("missing bearer token")

        try:
            claims: Dict[str, Any] = jwt.decode(
                raw,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "sub", "iss"]},
            )
        except InvalidTokenError as exc:
            # Normalise every PyJWT failure (expired, bad signature, bad issuer,
            # missing claim, malformed) into the domain auth error.
            raise Unauthenticated("invalid bearer token") from exc

        return self._principal_from_claims(claims)

    def authorize(self, principal: Principal, request: AuthorizationRequest) -> None:
        """Assert ``principal`` may perform ``request``; raise otherwise.

        Enforces the RBAC grant matrix plus per-appeal ownership
        (``request.resource_id`` against ``principal.owned_ids``). Returns
        ``None`` on success and raises :class:`Forbidden` when denied.
        """
        grants = _ROLE_GRANTS.get(principal.role)
        verb = (request.action, request.resource)
        if grants is None or verb not in grants:
            raise Forbidden(
                f"role {principal.role!r} may not {request.action} {request.resource}"
            )

        ownership_checked = (
            verb in _OWNERSHIP_REQUIRED
            and principal.role not in _OWNERSHIP_EXEMPT_ROLES
        )
        if ownership_checked and request.resource_id not in principal.owned_ids:
            raise Forbidden(
                f"principal does not own {request.resource} {request.resource_id!r}"
            )

    @staticmethod
    def _principal_from_claims(claims: Mapping[str, Any]) -> Principal:
        """Build a :class:`Principal` from verified JWT claims.

        ``sub`` is the subject; ``role`` defaults to a powerless empty role when
        absent; ``owned_ids`` comes from the ``owned`` claim (a list of strings).
        Malformed claim shapes raise :class:`Unauthenticated`.
        """
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise Unauthenticated("token has no subject")

        role = claims.get("role", "")
        if not isinstance(role, str):
            raise Unauthenticated("token role claim is malformed")

        owned_raw = claims.get("owned", [])
        if not isinstance(owned_raw, list) or not all(
            isinstance(item, str) for item in owned_raw
        ):
            raise Unauthenticated("token owned claim is malformed")

        return Principal(
            subject=subject,
            role=role,
            owned_ids=frozenset(owned_raw),
        )
