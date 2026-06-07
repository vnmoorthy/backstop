"""Contract suite for :class:`ReasoningPort`, parametrized over both adapters.

The same test bodies run against BOTH implementations to prove substitutability:

* the SIM adapter (:class:`LocalReasoningAdapter`) runs for real — it is a
  genuine offline grounded-NLG engine;
* the REAL adapter (:class:`MiniMaxReasoningAdapter`) runs against an in-process
  ``httpx.MockTransport`` so NO network is touched and a missing vendor SDK is
  irrelevant (we drive the transport directly).

Asserted invariants (identical for both adapters):

* ``compose_line`` returns a line of at most ``max_words`` words;
* ``citations`` is always a SUBSET of the supplied evidence ids — the model can
  never fabricate a citation;
* insufficient/ungrounded evidence yields the deterministic safe-fallback line
  with ``grounded=False`` and no citations;
* ``compose_line.dialog_act`` is a valid :class:`DialogAct`;
* ``interpret_denial`` returns ``recommended_route`` in :class:`RouteDecision`
  and ``next_dialog_act`` in :class:`DialogAct`;
* ``health`` reports the correct :class:`IntegrationMode` and never raises.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import pytest

from backstop.adapters.minimax import (
    LocalReasoningAdapter,
    MiniMaxReasoningAdapter,
    MiniMaxSettings,
)
from backstop.adapters.minimax._grounding import SAFE_FALLBACK_TEXT
from backstop.domain.carc_table import load_carc_table
from backstop.domain.enums import DialogAct, IntegrationMode, RouteDecision
from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.reasoning_port import (
    ComposeLineRequest,
    EvidenceSnippet,
    InterpretDenialRequest,
    ReasoningPort,
)


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def red(text: str) -> RedactedText:
    """Build a RedactedText for test inputs via the sanctioned factory."""
    return RedactedText.from_redaction(text, SANCTIONED_TOKEN)


def evidence(*pairs: Any) -> tuple:
    """Build a tuple of EvidenceSnippet from (id, text[, score]) tuples."""
    snippets: List[EvidenceSnippet] = []
    for item in pairs:
        if len(item) == 3:
            sid, text, score = item
        else:
            sid, text = item
            score = None
        snippets.append(EvidenceSnippet(snippet_id=sid, text=red(text), score=score))
    return tuple(snippets)


# A small canned MiniMax "model brain": it reads the user payload and returns a
# grounded compose JSON or a structured interpret JSON. This keeps the REAL
# adapter exercised end-to-end (request shaping -> transport -> parsing ->
# guardrails) without a network or a real SDK.
def _mock_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode("utf-8"))
    messages = body["messages"]
    system = messages[0]["content"]
    user = messages[1]["content"]

    payload = _interpret_payload(user) if "classify" in system else _compose_payload(user)

    payload["base_resp"] = {"status_code": 0, "status_msg": "success"}
    payload["model"] = body["model"]
    payload["usage"] = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
    return httpx.Response(200, json=payload)


def _compose_payload(user: str) -> Dict[str, Any]:
    """Return a grounded compose JSON citing the first evidence id in the user msg."""
    ids = _evidence_ids(user)
    if not ids:
        # No evidence -> model refuses with an ungrounded reply.
        content = json.dumps(
            {"line": "", "dialog_act": "PROVIDE_INFO", "citations": [], "grounded": False}
        )
    else:
        content = json.dumps(
            {
                "line": "Per the plan policy the service is covered and supported.",
                "dialog_act": "cite_policy",
                "citations": [ids[0]],
                "grounded": True,
                "confidence": 0.81,
            }
        )
    return _as_completion(content)


def _interpret_payload(user: str) -> Dict[str, Any]:
    """Return a structured interpret JSON for the denial in the user msg."""
    content = json.dumps(
        {
            "category": "medical_necessity",
            "carc": "50",
            "rarc": None,
            "rebuttal_hook": "Rebut the medical-necessity basis with the LCD record.",
            "recommended_route": "peer_to_peer",
            "next_dialog_act": "cite_policy",
            "ambiguous": False,
        }
    )
    return _as_completion(content)


def _as_completion(content: str) -> Dict[str, Any]:
    return {
        "id": "cc-1",
        "object": "chat.completion",
        "created": 0,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def _evidence_ids(user: str) -> List[str]:
    ids: List[str] = []
    for line in user.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and ":" in stripped:
            ids.append(stripped[2:].split(":", 1)[0].strip())
    return ids


# --------------------------------------------------------------------------- #
# Fixtures: the two adapters under one interface.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sim_adapter() -> LocalReasoningAdapter:
    return LocalReasoningAdapter(carc_table=load_carc_table())


@pytest.fixture()
def real_adapter() -> MiniMaxReasoningAdapter:
    transport = httpx.MockTransport(_mock_handler)
    client = httpx.AsyncClient(
        base_url="https://api.minimax.io/v1", transport=transport
    )
    settings = MiniMaxSettings(
        api_key="test-key",
        base_url="https://api.minimax.io/v1",
        model="MiniMax-Text-01",
        group_id="grp-123",
        route="native",
    )
    return MiniMaxReasoningAdapter(http=client, settings=settings)


@pytest.fixture(params=["sim", "real"])
def adapter(
    request: pytest.FixtureRequest,
    sim_adapter: LocalReasoningAdapter,
    real_adapter: MiniMaxReasoningAdapter,
) -> ReasoningPort:
    return sim_adapter if request.param == "sim" else real_adapter


# --------------------------------------------------------------------------- #
# Structural: both honour the runtime-checkable Protocol.
# --------------------------------------------------------------------------- #
def test_both_adapters_are_reasoning_ports(
    sim_adapter: LocalReasoningAdapter, real_adapter: MiniMaxReasoningAdapter
) -> None:
    assert isinstance(sim_adapter, ReasoningPort)
    assert isinstance(real_adapter, ReasoningPort)


# --------------------------------------------------------------------------- #
# compose_line contract.
# --------------------------------------------------------------------------- #
async def test_compose_line_respects_word_cap(adapter: ReasoningPort) -> None:
    req = ComposeLineRequest(
        call_state=red("payer denied claim as not medically necessary"),
        evidence=evidence(
            ("e1", "The LCD policy criteria are met; the service is medically necessary.", 0.9),
            ("e2", "Prior authorization was on file for the procedure.", 0.4),
        ),
        max_words=12,
        dialog_act=DialogAct.CITE_POLICY,
    )
    result = await adapter.compose_line(req)
    assert len(str(result.line).split()) <= req.max_words


async def test_compose_citations_subset_of_evidence(adapter: ReasoningPort) -> None:
    supplied = evidence(
        ("e1", "The LCD policy criteria are met; the service is medically necessary.", 0.9),
        ("e2", "Prior authorization was on file for the procedure.", 0.4),
    )
    req = ComposeLineRequest(
        call_state=red("the denial says not medically necessary per policy"),
        evidence=supplied,
        max_words=40,
    )
    result = await adapter.compose_line(req)
    allowed = {snip.snippet_id for snip in supplied}
    assert set(result.citations).issubset(allowed)
    assert result.dialog_act in DialogAct
    if result.grounded:
        assert result.citations  # grounded implies at least one real citation


async def test_compose_never_fabricates_citations(adapter: ReasoningPort) -> None:
    # Only e1/e2 are supplied; no result may cite anything else, ever.
    supplied = evidence(
        ("e1", "Coverage policy supports the billed CPT for this diagnosis.", 0.8),
        ("e2", "The plan's medical policy lists this service as covered.", 0.7),
    )
    req = ComposeLineRequest(
        call_state=red("payer claims service not covered under the plan policy"),
        evidence=supplied,
        max_words=30,
    )
    result = await adapter.compose_line(req)
    for cid in result.citations:
        assert cid in {"e1", "e2"}


async def test_compose_ungrounded_returns_safe_fallback(adapter: ReasoningPort) -> None:
    # No evidence at all -> both adapters must return the deterministic fallback.
    req = ComposeLineRequest(
        call_state=red("payer says something ambiguous about the claim"),
        evidence=(),
        max_words=40,
    )
    result = await adapter.compose_line(req)
    assert result.grounded is False
    assert result.citations == ()
    assert str(result.line) == SAFE_FALLBACK_TEXT


# --------------------------------------------------------------------------- #
# interpret_denial contract.
# --------------------------------------------------------------------------- #
async def test_interpret_denial_returns_domain_enums(adapter: ReasoningPort) -> None:
    req = InterpretDenialRequest(
        denial_text=red("Service denied: not deemed a medical necessity by the plan."),
        carc="50",
    )
    result = await adapter.interpret_denial(req)
    assert isinstance(result.category, str) and result.category
    assert result.recommended_route in RouteDecision
    assert result.next_dialog_act in DialogAct
    assert isinstance(result.ambiguous, bool)


async def test_interpret_medical_necessity_routes_to_appeal_or_p2p(
    adapter: ReasoningPort,
) -> None:
    req = InterpretDenialRequest(
        denial_text=red("Denied as not medically necessary; see plan policy."),
        carc="50",
    )
    result = await adapter.interpret_denial(req)
    assert result.recommended_route in {RouteDecision.APPEAL, RouteDecision.PEER_TO_PEER}
    assert result.category == "medical_necessity"


# --------------------------------------------------------------------------- #
# health contract.
# --------------------------------------------------------------------------- #
async def test_health_reports_mode(
    sim_adapter: LocalReasoningAdapter, real_adapter: MiniMaxReasoningAdapter
) -> None:
    sim_health = await sim_adapter.health()
    assert sim_health.mode is IntegrationMode.SIM
    assert sim_health.ok is True

    real_health = await real_adapter.health()
    assert real_health.mode is IntegrationMode.REAL


# --------------------------------------------------------------------------- #
# Sim-only: genuine grounded NLG (not an echo) + determinism.
# --------------------------------------------------------------------------- #
async def test_sim_is_grounded_nlg_not_echo(sim_adapter: LocalReasoningAdapter) -> None:
    req_a = ComposeLineRequest(
        call_state=red("denial says not medically necessary per the plan policy"),
        evidence=evidence(
            ("p1", "The LCD coverage policy criteria are satisfied for this service.", 0.9),
            ("p2", "An unrelated note about mailing addresses.", 0.1),
        ),
        max_words=40,
    )
    req_b = ComposeLineRequest(
        call_state=red("payer asks for the prior authorization reference number"),
        evidence=evidence(
            ("q1", "Prior authorization 12345 was approved before the date of service.", 0.95),
            ("q2", "An unrelated note about mailing addresses.", 0.1),
        ),
        max_words=40,
    )
    res_a = await sim_adapter.compose_line(req_a)
    res_b = await sim_adapter.compose_line(req_b)
    # Different inputs -> genuinely different grounded lines citing different ids.
    assert str(res_a.line) != str(res_b.line)
    assert res_a.grounded and res_b.grounded
    assert res_a.citations == ("p1",)
    assert res_b.citations == ("q1",)


async def test_sim_is_deterministic(sim_adapter: LocalReasoningAdapter) -> None:
    req = ComposeLineRequest(
        call_state=red("denied as not medically necessary per policy"),
        evidence=evidence(
            ("e1", "The medical policy criteria for coverage are met.", 0.9),
        ),
        max_words=25,
    )
    first = await sim_adapter.compose_line(req)
    second = await sim_adapter.compose_line(req)
    assert str(first.line) == str(second.line)
    assert first.citations == second.citations
    assert first.dialog_act == second.dialog_act


async def test_sim_interpret_missing_info_resubmits(
    sim_adapter: LocalReasoningAdapter,
) -> None:
    req = InterpretDenialRequest(
        denial_text=red("Claim/service lacks information or has a submission error."),
        carc="16",
    )
    result = await sim_adapter.interpret_denial(req)
    assert result.category == "missing_information"
    assert result.recommended_route is RouteDecision.RESUBMIT
    assert result.ambiguous is False


async def test_sim_ambiguous_when_unclassifiable(
    sim_adapter: LocalReasoningAdapter,
) -> None:
    req = InterpretDenialRequest(
        denial_text=red("Please review the attached correspondence for details."),
    )
    result = await sim_adapter.interpret_denial(req)
    assert result.ambiguous is True
    assert result.recommended_route in RouteDecision
    assert result.next_dialog_act in DialogAct
