"""Security headers + CORS + per-appeal ownership authorization tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.controllers.conftest import ALLOWED_ORIGIN, auth_header


def _create_appeal(client: TestClient, owner_token_header: dict) -> str:
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
    resp = client.post("/v1/appeals", json=body, headers=owner_token_header)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_security_headers_present_on_responses(client: TestClient) -> None:
    """CSP + frame/content-type/referrer headers are on every response."""
    resp = client.get("/healthz")
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert "content-security-policy" in headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_cors_allows_listed_origin(client: TestClient) -> None:
    """A preflight from the allowlisted origin echoes that origin (no wildcard)."""
    resp = client.options(
        "/v1/triage",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_origin = resp.headers.get("access-control-allow-origin")
    assert allow_origin == ALLOWED_ORIGIN
    assert allow_origin != "*"


def test_cors_rejects_unlisted_origin(client: TestClient) -> None:
    """A preflight from an unlisted origin is not granted that origin."""
    resp = client.options(
        "/v1/triage",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_owner_can_read_their_appeal(client: TestClient) -> None:
    """An agent who owns the appeal id may read its redacted view."""
    # Admin creates; then read as an agent who owns that id.
    appeal_id = _create_appeal(client, auth_header(role="admin", subject="admin-1"))
    resp = client.get(
        f"/v1/appeals/{appeal_id}",
        headers=auth_header(role="agent", subject="agent-1", owned=[appeal_id]),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == appeal_id


def test_non_owner_is_forbidden(client: TestClient) -> None:
    """An agent who does not own the appeal id is refused with 403."""
    appeal_id = _create_appeal(client, auth_header(role="admin", subject="admin-1"))
    resp = client.get(
        f"/v1/appeals/{appeal_id}",
        headers=auth_header(role="agent", subject="agent-2", owned=["some-other"]),
    )
    assert resp.status_code == 403


def test_response_carries_no_raw_phi(client: TestClient) -> None:
    """The redacted appeal view never echoes the raw member/claim hashes' source."""
    appeal_id = _create_appeal(client, auth_header(role="admin", subject="admin-1"))
    resp = client.get(
        f"/v1/appeals/{appeal_id}",
        headers=auth_header(role="admin", subject="admin-1"),
    )
    payload = resp.json()
    # Only safe, non-PHI fields are present; no member id / claim number fields.
    assert set(payload.keys()) == {
        "id",
        "status",
        "route",
        "payer_id",
        "denial_code",
        "recoverable_cents",
        "sol_deadline",
        "needs_human_review",
    }
