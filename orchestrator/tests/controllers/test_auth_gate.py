"""Every non-health route is authn-gated; health probes are open.

Enumerates the route table and asserts that each non-health HTTP route rejects
an unauthenticated request with ``401`` and accepts a valid token, while
``/healthz`` and ``/readyz`` are reachable without any credential.
"""

from __future__ import annotations

from typing import List, Tuple

from fastapi.testclient import TestClient

from tests.controllers.conftest import auth_header

# (method, path, json-body-or-None) for one representative call per route.
_PROTECTED_ROUTES: List[Tuple[str, str]] = [
    ("GET", "/v1/triage"),
    ("GET", "/v1/review/queue"),
    ("GET", "/v1/appeals/ap-unknown"),
    ("GET", "/v1/review/ap-unknown/evidence"),
]


def test_health_probes_are_unauthenticated(client: TestClient) -> None:
    """``/healthz`` and ``/readyz`` are reachable with no credential."""
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_protected_routes_reject_missing_token(client: TestClient) -> None:
    """Every protected route returns 401 without a bearer token."""
    for method, path in _PROTECTED_ROUTES:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should be 401, got {resp.status_code}"


def test_protected_route_rejects_invalid_token(client: TestClient) -> None:
    """An unverifiable bearer token is rejected with 401."""
    resp = client.get("/v1/triage", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_protected_route_accepts_valid_token(client: TestClient) -> None:
    """A valid token reaches the handler (200, not 401)."""
    resp = client.get("/v1/triage", headers=auth_header(role="admin"))
    assert resp.status_code == 200


def test_create_appeal_requires_token(client: TestClient) -> None:
    """``POST /v1/appeals`` is authn-gated like every other write route."""
    body = {
        "denial": {
            "payer_id": "payer-1",
            "payer_name": "Acme",
            "plan": "PPO",
            "member_id_hash": "h1",
            "claim_number_hash": "c1",
            "denial_code": "197",
            "rarc": None,
            "cpt": ["99213"],
            "billed_cents": 50000,
            "dos": "2026-06-01",
            "npis": ["1234567890"],
        },
        "recoverable_cents": 50000,
        "sol_deadline": "2026-07-07",
        "needs_human_review": False,
    }
    assert client.post("/v1/appeals", json=body).status_code == 401
    ok = client.post("/v1/appeals", json=body, headers=auth_header(role="admin"))
    assert ok.status_code == 201
