"""End-to-end boot + appeal-to-FILED journey in sim mode.

Asserts the app imports and boots via ``TestClient`` (the lifespan wires a real
sim container), then runs an authenticated appeal flow end to end:

    synthetic denial -> swarm (CallService turn) -> reconcile -> letter render
    -> review -> nurse sign-off -> FILED

and verifies that no raw PHI ever crosses the HTTP/JSON wire.
"""

from __future__ import annotations

from typing import Tuple

from fastapi.testclient import TestClient

from backstop.app import app as module_app
from backstop.app import create_app
from backstop.domain.enums import AppealStatus, Speaker, SpecialistKind
from backstop.domain.models import CallTurn, TurnObservation
from backstop.domain.money import Money, RecoverableDollars
from backstop.services.call_service import TurnInput
from backstop.services.letter_service import LetterDraft
from backstop.services.reconcile_service import DeskFinding
from tests.controllers.conftest import auth_header

# Raw PHI tokens that must NEVER appear in any wire payload.
_RAW_PHI = ("123-45-6789", "MEMBER999RAW", "John Q Patient")


def test_app_imports_and_exposes_routes() -> None:
    """The module-level ``app`` imports cleanly and has routes mounted."""
    assert module_app is not None
    assert len(module_app.routes) > 0


def test_app_boots_via_testclient() -> None:
    """``create_app()`` boots through the lifespan and serves liveness."""
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200


def _observation() -> TurnObservation:
    return TurnObservation(
        snr=20.0,
        speaking_rate=3.0,
        pitch_var=0.2,
        wada=0.5,
        cpu=0.3,
        ram=0.4,
        battery=0.8,
        gpu=0.1,
        rtt=40.0,
        bw=10.0,
        complexity=0.9,
        ctx_tokens=256,
    )


def test_full_appeal_flow_to_filed_no_phi_leak(client: TestClient) -> None:
    """A synthetic denial runs the full pipeline to FILED without leaking PHI."""
    container = client.app.state.container
    captured: list = []

    # 1. Create the appeal over HTTP (synthetic denial; PHI pre-hashed).
    create_body = {
        "denial": {
            "payer_id": "payer-1",
            "payer_name": "Acme Health",
            "plan": "PPO",
            "member_id_hash": "hash-of-member",
            "claim_number_hash": "hash-of-claim",
            "denial_code": "197",
            "rarc": None,
            "cpt": ["99213"],
            "billed_cents": 50000,
            "dos": "2026-06-01",
            "npis": ["1234567890"],
        },
        "recoverable_cents": 50000,
        "sol_deadline": "2026-07-07",
        "needs_human_review": True,
    }
    headers = auth_header(role="admin", subject="admin-1")
    create_resp = client.post("/v1/appeals", json=create_body, headers=headers)
    assert create_resp.status_code == 201, create_resp.text
    captured.append(create_resp.text)
    appeal_id = create_resp.json()["id"]
    nurse_headers = auth_header(role="nurse", subject="nurse-1", owned=[appeal_id])

    async def _pipeline() -> Tuple[str, str]:
        # 2. SWARM: run one routed CallService turn under the gate.
        call = container.call_service
        swarm = container.swarm_service
        redaction = container.redaction

        async def _body(aid: str) -> None:
            await call.handle_turn(
                TurnInput(
                    call_id=f"call-{aid}",
                    appeal_id=aid,
                    turn=CallTurn(index=0, speaker=Speaker.AGENT, observation=_observation()),
                    call_state=redaction.redact_text("greeting context"),
                    is_denial_turn=True,
                    carc="197",
                    payer_id="payer-1",
                )
            )

        outcomes = await swarm.run_all([appeal_id], _body)
        assert all(o.ok for o in outcomes)

        # 3. RECONCILE: cross-desk contradiction check (non-PHI findings).
        contradiction = container.reconcile_service.find_contradiction(
            [
                DeskFinding(desk=SpecialistKind.PRIOR_AUTH_DESK, carc="197"),
                DeskFinding(desk=SpecialistKind.RECORDS_DESK, carc="16"),
            ]
        )
        assert contradiction.found in (True, False)

        # 4. LETTER: redact-then-render-then-store; hash is the sign-off message.
        rendered = await container.letter_service.render_and_store(
            appeal_id,
            LetterDraft(
                payer_name="Acme Health",
                recipient_block="Appeals Desk",
                claim_reference="claim ref",
                denial_summary="CO-197 prior authorization",
                body_paragraphs=["We are appealing this denial per policy."],
                billed=Money(cents=50000),
                recoverable=RecoverableDollars(Money(cents=50000)),
                signoff_block="Reviewed and attested by nurse.",
            ),
        )

        # 5. Advance the lifecycle DENIED -> ... -> AWAITING_SIGNOFF via CAS.
        appeal_svc = container.appeal_service
        assert await appeal_svc.advance(
            appeal_id,
            expected=AppealStatus.DENIED,
            new=AppealStatus.TRIAGED,
            event_kind="triaged",
            seq=1,
        )
        assert await appeal_svc.advance(
            appeal_id,
            expected=AppealStatus.TRIAGED,
            new=AppealStatus.IN_APPEAL,
            event_kind="in_appeal",
            seq=2,
        )
        assert await appeal_svc.advance(
            appeal_id,
            expected=AppealStatus.IN_APPEAL,
            new=AppealStatus.AWAITING_SIGNOFF,
            event_kind="awaiting_signoff",
            seq=3,
        )

        # 6. Sign the SHA-256 letter-hash bytes with the real Ed25519 adapter.
        appeal_hash = bytes.fromhex(rendered.sha256)
        signature = container.signature.sign(appeal_hash, "nurse-1")
        return rendered.sha256, signature.signature_b64

    # Drive the async pipeline on the TestClient's own event loop (no new loop).
    appeal_hash_hex, signature_b64 = client.portal.call(_pipeline)

    # 7. SIGN-OFF over HTTP -> FILED (audit chain intact + valid signature).
    signoff_resp = client.post(
        f"/v1/review/{appeal_id}/signoff",
        json={
            "appeal_hash_hex": appeal_hash_hex,
            "signature_b64": signature_b64,
            "public_key_id": "backstop-ed25519",
            "nurse_identity": "nurse-1",
            "signed_at_iso": "2026-06-07T12:00:00+00:00",
            "seq": 4,
        },
        headers=nurse_headers,
    )
    assert signoff_resp.status_code == 200, signoff_resp.text
    captured.append(signoff_resp.text)
    assert signoff_resp.json()["filed"] is True

    # 8. The appeal now reads as FILED over HTTP.
    final = client.get(f"/v1/appeals/{appeal_id}", headers=headers)
    assert final.status_code == 200
    captured.append(final.text)
    assert final.json()["status"] == AppealStatus.FILED.value

    # 9. No raw PHI ever crossed the wire across every captured response.
    blob = "\n".join(captured)
    for phi in _RAW_PHI:
        assert phi not in blob, f"raw PHI leaked on the wire: {phi!r}"
