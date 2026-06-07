"""Auth adapters: JWT authentication + RBAC authorization for the app edge.

Houses :class:`~backstop.adapters.auth.jwt_auth_adapter.JwtAuthAdapter`, which
implements :class:`backstop.ports.auth_port.AuthPort` by verifying a signed JWT
bearer token and gating ``(action, resource)`` against the principal's role and
owned ids. The ``PyJWT`` library is imported lazily inside the verify path.
"""

from __future__ import annotations
