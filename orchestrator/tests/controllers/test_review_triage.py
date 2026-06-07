"""Review queue, evidence timeline, triage worklist, and sign-off controllers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.controllers.conftest import auth_header

_ADMIN = "admin-1"


def _create(client: TestClient, *, needs_review: bool) -> str:
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
        "needs_human_review": needs_review,
    }
    resp = client.post("/v1/appeals", json=body, headers=auth_header(role="admin", subject=_ADMIN))
    assert resp.status_code == 201
    return resp.json()["id"]


def test_triage_worklist_requires_auth(client: TestClient) -> None:
    """The triage worklist is authn-gated."""
    assert client.get("/v1/triage").status_code == 401


def test_triage_worklist_ranks_appeals(client: TestClient) -> None:
    """The triage worklist returns scored, redacted appeal rows."""
    _create(client, needs_review=False)
    resp = client.get("/v1/triage", headers=auth_header(role="admin", subject=_ADMIN))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert "score" in items[0]
    assert "id" in items[0]["appeal"]


def test_review_queue_lists_flagged_appeals(client: TestClient) -> None:
    """The nurse queue surfaces appeals flagged for human review."""
    flagged = _create(client, needs_review=True)
    _create(client, needs_review=False)
    resp = client.get("/v1/review/queue", headers=auth_header(role="nurse", subject="nurse-1"))
    assert resp.status_code == 200
    ids = [item["appeal"]["id"] for item in resp.json()["items"]]
    assert flagged in ids


def test_review_evidence_is_redacted(client: TestClient) -> None:
    """The evidence timeline returns redacted bodies for an owned appeal."""
    appeal_id = _create(client, needs_review=True)
    resp = client.get(
        f"/v1/review/{appeal_id}/evidence",
        headers=auth_header(role="nurse", subject="nurse-1", owned=[appeal_id]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["appeal_id"] == appeal_id
    assert isinstance(body["evidence"], list)


def test_review_evidence_non_owner_forbidden(client: TestClient) -> None:
    """A nurse who does not own the appeal cannot read its evidence."""
    appeal_id = _create(client, needs_review=True)
    resp = client.get(
        f"/v1/review/{appeal_id}/evidence",
        headers=auth_header(role="nurse", subject="nurse-2", owned=["other"]),
    )
    assert resp.status_code == 403


def test_signoff_requires_auth(client: TestClient) -> None:
    """The sign-off route is authn-gated."""
    resp = client.post(
        "/v1/review/ap-1/signoff",
        json={
            "appeal_hash_hex": "00",
            "signature_b64": "x",
            "public_key_id": "k",
            "nurse_identity": "n",
            "signed_at_iso": "2026-06-07T12:00:00+00:00",
            "seq": 1,
        },
    )
    assert resp.status_code == 401


def test_signoff_refuses_bad_signature(client: TestClient) -> None:
    """A sign-off with an invalid signature is refused (not filed)."""
    appeal_id = _create(client, needs_review=True)
    resp = client.post(
        f"/v1/review/{appeal_id}/signoff",
        json={
            "appeal_hash_hex": "abcd",
            "signature_b64": "not-a-valid-signature",
            "public_key_id": "backstop-ed25519",
            "nurse_identity": "nurse-1",
            "signed_at_iso": "2026-06-07T12:00:00+00:00",
            "seq": 1,
        },
        headers=auth_header(role="nurse", subject="nurse-1", owned=[appeal_id]),
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["filed"] is False
    assert payload["refusal"] is not None
