"""AuthService — authenticate-then-authorize use-case over :class:`AuthPort`.

Controllers call this service at every protected edge: it turns a bearer token
into a :class:`Principal` and, in one call, asserts the principal may perform a
``(action, resource[, resource_id])`` request. Authorization failures surface as
the domain :class:`Forbidden`; a missing/bad token surfaces as
:class:`Unauthenticated`. The service holds no state and depends only on the
injected :class:`AuthPort`.
"""

from __future__ import annotations

from typing import Optional

from backstop.ports.auth_port import (
    AuthorizationRequest,
    AuthPort,
    Principal,
)


class AuthService:
    """Authenticate a token and gate a single ``(action, resource)`` request."""

    def __init__(self, auth: AuthPort) -> None:
        """Store the injected authentication/authorization port."""
        self._auth = auth

    def authenticate(self, token: str) -> Principal:
        """Verify ``token`` and return the authenticated :class:`Principal`.

        Raises:
            Unauthenticated: For a missing, malformed, expired, or badly-signed
                token (translated by the adapter from any vendor exception).
        """
        return self._auth.authenticate(token)

    def authorize(
        self,
        principal: Principal,
        action: str,
        resource: str,
        resource_id: str,
    ) -> None:
        """Assert ``principal`` may perform ``action`` on ``resource``.

        Raises:
            Forbidden: When RBAC or per-appeal ownership denies the request.
        """
        self._auth.authorize(
            principal,
            AuthorizationRequest(
                action=action,
                resource=resource,
                resource_id=resource_id,
            ),
        )

    def check(
        self,
        token: str,
        action: str,
        resource: str,
        resource_id: str,
    ) -> Principal:
        """Authenticate ``token`` then authorize the request in one step.

        Returns the :class:`Principal` so the caller can attribute the action.

        Raises:
            Unauthenticated: If the token is invalid.
            Forbidden: If the authenticated principal may not act.
        """
        principal = self._auth.authenticate(token)
        self.authorize(principal, action, resource, resource_id)
        return principal

    def principal_owns(
        self, principal: Principal, resource_id: Optional[str]
    ) -> bool:
        """Return whether ``principal`` owns ``resource_id`` (``None`` -> False)."""
        if resource_id is None:
            return False
        return resource_id in principal.owned_ids
