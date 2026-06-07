"""Ingestion controller: auth, content-type validation, and size caps."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backstop.infra.config import Settings
from tests.controllers.conftest import auth_header

_X12 = b"ST*835*0001~CLP*CLAIM*22*100*0~CAS*CO*197*100~"


def test_ingestion_requires_auth(client: TestClient) -> None:
    """The upload route is authn-gated."""
    resp = client.post(
        "/v1/ingestion",
        data={"appeal_id": "ap-1", "kind": "X12_835"},
        files={"file": ("denial.edi", _X12, "text/plain")},
    )
    assert resp.status_code == 401


def test_ingestion_accepts_valid_edi(client: TestClient) -> None:
    """A valid text/plain EDI artifact is parsed (sim parser) and summarised."""
    resp = client.post(
        "/v1/ingestion",
        data={"appeal_id": "ap-1", "kind": "X12_835"},
        files={"file": ("denial.edi", _X12, "text/plain")},
        headers=auth_header(role="agent", owned=["ap-1"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["appeal_id"] == "ap-1"
    assert "overall_confidence" in body


def test_ingestion_rejects_wrong_content_type(client: TestClient) -> None:
    """A disallowed content type is rejected with 415."""
    resp = client.post(
        "/v1/ingestion",
        data={"appeal_id": "ap-1", "kind": "X12_835"},
        files={"file": ("evil.exe", b"MZ\x90\x00", "application/x-msdownload")},
        headers=auth_header(role="agent", owned=["ap-1"]),
    )
    assert resp.status_code == 415


def test_ingestion_rejects_oversized_upload(client: TestClient) -> None:
    """An upload exceeding the configured cap is rejected with 413."""
    cap = Settings().max_upload_bytes
    oversized = b"A" * (cap + 1)
    resp = client.post(
        "/v1/ingestion",
        data={"appeal_id": "ap-1", "kind": "X12_835"},
        files={"file": ("big.edi", oversized, "text/plain")},
        headers=auth_header(role="agent", owned=["ap-1"]),
    )
    assert resp.status_code == 413


def test_ingestion_rejects_empty_upload(client: TestClient) -> None:
    """An empty upload is rejected (422)."""
    resp = client.post(
        "/v1/ingestion",
        data={"appeal_id": "ap-1", "kind": "X12_835"},
        files={"file": ("empty.edi", b"", "text/plain")},
        headers=auth_header(role="agent", owned=["ap-1"]),
    )
    assert resp.status_code == 422


def test_ingestion_non_owner_forbidden(client: TestClient) -> None:
    """A principal that does not own the appeal id may not ingest for it."""
    resp = client.post(
        "/v1/ingestion",
        data={"appeal_id": "ap-1", "kind": "X12_835"},
        files={"file": ("denial.edi", _X12, "text/plain")},
        headers=auth_header(role="agent", owned=["other"]),
    )
    assert resp.status_code == 403
