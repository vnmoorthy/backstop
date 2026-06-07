"""In-test fake ports for the service-layer suites.

These doubles implement the L2 port Protocols directly — no concrete adapter is
imported. They do genuine local work where the test depends on it:

* :class:`FakeRedaction` is the *only* sanctioned producer of ``RedactedText``
  in the tests, minting it through ``RedactedText.from_redaction`` with the
  domain's sanctioned token (a raw ``str`` could not be substituted).
* :class:`SemaphoreGate` is a real ``asyncio.Semaphore`` whose (cap+1)th acquire
  genuinely suspends, with a live ``in_use`` meter and a recorded high-water
  mark — so concurrency-cap assertions are meaningful.
* :class:`MemoryAppealRepo` is a real in-memory CAS + append-only event log.

Every fake records the calls a test needs to assert ordering on.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import (
    AsyncIterator,
    Dict,
    List,
    Optional,
    Tuple,
)

from backstop.domain.enums import (
    AppealStatus,
    ArtifactKind,
    DialogAct,
    IntegrationMode,
    ModelTier,
)
from backstop.domain.errors import AppealNotFound, ChannelNotFound, Forbidden, Unauthenticated
from backstop.domain.models import Appeal, TurnObservation
from backstop.domain.redacted import SANCTIONED_TOKEN, RedactedText
from backstop.ports.appeal_repository_port import (
    AppealEventRecord,
    AppealFilter,
    AppealPage,
)
from backstop.ports.audit_log_port import AuditRecord
from backstop.ports.auth_port import AuthorizationRequest, Principal
from backstop.ports.concurrency_gate_port import CapacitySnapshot, Slot
from backstop.ports.denial_parser_port import (
    DenialExtraction,
    ExtractedField,
    ParseRequest,
)
from backstop.ports.event_bus_port import RedactedEvent
from backstop.ports.reasoning_port import (
    ComposeLineRequest,
    ComposeLineResult,
    DenialInterpretation,
    InterpretDenialRequest,
    ReasoningHealth,
)
from backstop.ports.redaction_port import Message, RedactedMessage
from backstop.ports.retrieval_port import (
    EvidenceChunk,
    RetrievalHealth,
    RetrievalQuery,
    RetrievalResult,
)
from backstop.ports.routing_port import RoutingDecision, RoutingExplanation
from backstop.ports.signature_port import Signature
from backstop.ports.speech_synthesis_port import (
    AudioFrame,
    SynthHealth,
    SynthRequest,
    SynthResult,
)
from backstop.ports.voice_transport_port import (
    Channel,
    Claims,
    JoinToken,
    Participant,
)

# A monotonic call recorder shared across fakes within one test, used to assert
# the relative ordering of cross-port calls (e.g. route before synth).
CallLog = List[str]


def make_redacted(text: str) -> RedactedText:
    """Mint a :class:`RedactedText` the way the redaction adapter would."""
    return RedactedText.from_redaction(text, SANCTIONED_TOKEN)


class FakeClock:
    """Deterministic clock returning a fixed instant and a manual monotonic."""

    def __init__(self, instant: Optional[datetime] = None) -> None:
        """Store the fixed instant (defaults to a stable UTC time)."""
        self._instant = instant or datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
        self._mono = 0.0

    def now(self) -> datetime:
        """Return the fixed wall-clock instant."""
        return self._instant

    def monotonic(self) -> float:
        """Return a non-decreasing counter, advancing one tick per call."""
        self._mono += 1.0
        return self._mono


class FakeIdGen:
    """Deterministic, monotonic id generator."""

    def __init__(self, prefix: str = "id") -> None:
        """Store the id prefix and zero the counter."""
        self._prefix = prefix
        self._n = 0

    def new_id(self) -> str:
        """Return the next ``<prefix>-NNNN`` id."""
        self._n += 1
        return f"{self._prefix}-{self._n:04d}"


class FakeRedaction:
    """Sole test producer of ``RedactedText``; masks a tiny PHI pattern."""

    def __init__(self, log: Optional[CallLog] = None) -> None:
        """Optionally record each call into a shared ordering log."""
        self.log = log if log is not None else []

    def redact_text(self, text: str) -> RedactedText:
        """Mask any ``MEMBER`` token and mint sanctioned ``RedactedText``."""
        self.log.append("redact")
        scrubbed = text.replace("MEMBER123", "[MEMBER_ID]")
        return make_redacted(scrubbed)

    def redact_messages(self, msgs: List[Message]) -> List[RedactedMessage]:
        """Redact every message, preserving role and order."""
        return [
            RedactedMessage(role=m.role, content=self.redact_text(m.content))
            for m in msgs
        ]

    def contains_phi(self, text: str) -> bool:
        """Return whether the raw PHI token still appears."""
        return "MEMBER123" in text


class RecordingRouter:
    """Router fake that returns a fixed tier and records every ``route`` call."""

    def __init__(self, tier: ModelTier, log: Optional[CallLog] = None) -> None:
        """Store the tier to return and the shared ordering log."""
        self._tier = tier
        self.log = log if log is not None else []
        self.route_calls = 0

    def route(self, obs: TurnObservation) -> RoutingDecision:
        """Record the call and return the fixed tier (one argmax)."""
        self.route_calls += 1
        self.log.append("route")
        return RoutingDecision(tier=self._tier, profile_idx=0, confidence=0.9)

    def explain(self, obs: TurnObservation) -> RoutingExplanation:
        """Return a minimal explanation for the fixed tier."""
        return RoutingExplanation(
            profile_idx=0,
            tier=self._tier,
            top_logits=((0, 1.0),),
            confidence=0.9,
            value_estimate=0.0,
            coupling_infeasible=False,
        )

    def is_feasible(self, tier: ModelTier, obs: TurnObservation) -> bool:
        """Treat every tier as feasible for the fixed-tier router."""
        return True

    def tiers(self) -> Tuple[ModelTier, ...]:
        """Return the three tiers, premium-to-fast."""
        return (
            ModelTier.CLOUD_PREMIUM,
            ModelTier.HYBRID_BALANCED,
            ModelTier.ONDEVICE_FAST,
        )


class RecordingReasoning:
    """Reasoning fake that records calls and echoes evidence ids as citations."""

    def __init__(self, log: Optional[CallLog] = None) -> None:
        """Store the shared ordering log and zero the call counters."""
        self.log = log if log is not None else []
        self.compose_calls = 0
        self.interpret_calls = 0

    async def compose_line(self, req: ComposeLineRequest) -> ComposeLineResult:
        """Compose a grounded line citing only the supplied evidence ids."""
        self.compose_calls += 1
        self.log.append("compose")
        ids = tuple(e.snippet_id for e in req.evidence)
        return ComposeLineResult(
            line=make_redacted("We are appealing this denial per policy."),
            dialog_act=DialogAct.REBUT,
            citations=ids,
            grounded=bool(ids),
            confidence=0.8,
        )

    async def interpret_denial(
        self, req: InterpretDenialRequest
    ) -> DenialInterpretation:
        """Return a fixed interpretation (unused by most service tests)."""
        from backstop.domain.enums import RouteDecision

        self.interpret_calls += 1
        return DenialInterpretation(
            category="prior_auth",
            carc=req.carc,
            rarc=req.rarc,
            rebuttal_hook="auth on file",
            recommended_route=RouteDecision.APPEAL,
            next_dialog_act=DialogAct.REBUT,
            ambiguous=False,
        )

    async def health(self) -> ReasoningHealth:
        """Report a healthy sim backend."""
        return ReasoningHealth(ok=True, mode=IntegrationMode.SIM)


class RecordingRetrieval:
    """Retrieval fake returning one fixed chunk and recording calls."""

    def __init__(self, log: Optional[CallLog] = None) -> None:
        """Store the shared ordering log and zero the call counter."""
        self.log = log if log is not None else []
        self.retrieve_calls = 0
        self.last_query: Optional[RetrievalQuery] = None

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Record the call/query and return one runbook chunk."""
        self.retrieve_calls += 1
        self.last_query = query
        self.log.append("retrieve")
        chunk = EvidenceChunk(
            chunk_id="runbook-1",
            text="Prior authorization was obtained; see auth ref.",
            score=0.91,
            source="runbook_co197.md",
        )
        return RetrievalResult(chunks=(chunk,), query_id="q-1")

    async def health(self) -> RetrievalHealth:
        """Report a healthy sim backend."""
        return RetrievalHealth(ok=True, mode=IntegrationMode.SIM)


class RecordingSpeech:
    """Speech fake producing length-correct bytes and recording the text."""

    def __init__(self, log: Optional[CallLog] = None) -> None:
        """Store the shared ordering log and the captured request list."""
        self.log = log if log is not None else []
        self.requests: List[SynthRequest] = []

    async def synth(self, req: SynthRequest) -> SynthResult:
        """Record the request and return WAV-ish bytes scaled to text length."""
        self.requests.append(req)
        self.log.append("synth")
        n = max(1, len(str(req.text)))
        return SynthResult(
            audio=b"RIFF" + b"\x00" * n,
            duration_ms=n * 50,
            cost_chars=n,
            sample_rate=req.sample_rate,
        )

    async def synth_stream(self, req: SynthRequest) -> AsyncIterator[AudioFrame]:
        """Yield a single terminal frame for the redacted line."""
        yield AudioFrame(pcm=b"\x00\x00", seq=0, is_final=True)

    async def health(self) -> SynthHealth:
        """Report a healthy sim backend."""
        return SynthHealth(ok=True, mode=IntegrationMode.SIM)


class RecordingEventBus:
    """Event-bus fake that records every published redacted event."""

    def __init__(self, log: Optional[CallLog] = None) -> None:
        """Store the shared ordering log and the published-event list."""
        self.log = log if log is not None else []
        self.published: List[Tuple[str, RedactedEvent]] = []

    async def publish(self, channel: str, event: RedactedEvent) -> None:
        """Record the published event (its body is already ``RedactedText``)."""
        self.log.append("publish")
        self.published.append((channel, event))

    def subscribe(self, channel: str, principal: object) -> AsyncIterator[RedactedEvent]:
        """Return an empty async stream (unused by service tests)."""

        async def _empty() -> AsyncIterator[RedactedEvent]:
            return
            yield  # pragma: no cover - makes this an async generator

        return _empty()

    async def close(self) -> None:
        """No-op close for the in-memory bus."""


class SemaphoreGate:
    """A genuine ``asyncio.Semaphore`` admission gate with a high-water mark."""

    def __init__(self, capacity: int) -> None:
        """Build the gate with ``capacity`` slots and a live usage meter."""
        self._capacity = capacity
        self._sem = asyncio.Semaphore(capacity)
        self._in_use = 0
        self.max_in_use = 0
        self._n = 0

    async def acquire(self, *, slot_key: str, timeout: Optional[float] = None) -> Slot:
        """Acquire a slot (genuinely suspends past capacity)."""
        await self._sem.acquire()
        self._in_use += 1
        self.max_in_use = max(self.max_in_use, self._in_use)
        self._n += 1
        return Slot(slot_id=f"slot-{self._n}", slot_key=slot_key)

    async def release(self, slot: Slot) -> None:
        """Release a held slot back to the pool (idempotent per acquire)."""
        self._in_use -= 1
        self._sem.release()

    def slot(self, slot_key: str) -> _SlotCtx:
        """Return an async context manager that acquires/releases a slot."""
        return _SlotCtx(self, slot_key)

    async def ensure_capacity(self, *, target: int) -> int:
        """Return the (fixed) capacity; the sim pool does not grow."""
        return self._capacity

    async def capacity(self) -> CapacitySnapshot:
        """Return a live capacity snapshot."""
        return CapacitySnapshot(
            capacity=self._capacity,
            in_use=self._in_use,
            available=self._capacity - self._in_use,
            mode=IntegrationMode.SIM,
        )

    async def reconcile(self) -> int:
        """Reclaim nothing on startup for the in-process gate."""
        return 0

    async def aclose(self) -> None:
        """No-op close for the in-process gate."""


@asynccontextmanager
async def _slot_cm(gate: SemaphoreGate, slot_key: str) -> AsyncIterator[Slot]:
    """Acquire a slot, yield it, and guarantee release in ``finally``."""
    slot = await gate.acquire(slot_key=slot_key)
    try:
        yield slot
    finally:
        await gate.release(slot)


class _SlotCtx:
    """Thin async-context wrapper so ``gate.slot(key)`` is awaitable-with."""

    def __init__(self, gate: SemaphoreGate, slot_key: str) -> None:
        """Store the gate and key; defer acquisition to ``__aenter__``."""
        self._cm = _slot_cm(gate, slot_key)

    async def __aenter__(self) -> Slot:
        """Acquire and return the slot."""
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc: object) -> None:
        """Release the slot (always runs, even on exception)."""
        await self._cm.__aexit__(*exc)


class MemoryAppealRepo:
    """In-memory CAS + append-only event log honouring the repo contract."""

    def __init__(self) -> None:
        """Initialise empty aggregate and per-appeal event stores."""
        self._appeals: Dict[str, Appeal] = {}
        self._events: Dict[str, List[AppealEventRecord]] = {}
        self.cas_calls: List[Tuple[str, AppealStatus, AppealStatus]] = []

    async def save(self, appeal: Appeal) -> None:
        """Insert or replace an aggregate; seed its empty event log."""
        self._appeals[appeal.id] = appeal
        self._events.setdefault(appeal.id, [])

    async def load(self, appeal_id: str) -> Appeal:
        """Return the aggregate or raise ``AppealNotFound``."""
        try:
            return self._appeals[appeal_id]
        except KeyError as exc:
            raise AppealNotFound(appeal_id) from exc

    async def list(
        self,
        filter: AppealFilter,
        *,
        limit: int,
        offset: int,
    ) -> AppealPage:
        """Return a filtered, windowed page of aggregates."""
        items = [
            a
            for a in self._appeals.values()
            if (filter.status is None or a.status is filter.status)
            and (filter.payer_id is None or a.denial.payer.payer_id == filter.payer_id)
            and (
                filter.needs_human_review is None
                or a.needs_human_review == filter.needs_human_review
            )
        ]
        total = len(items)
        window = items[offset : offset + limit]
        return AppealPage(items=window, total=total, limit=limit, offset=offset)

    async def update_status_atomic(
        self,
        appeal_id: str,
        expected: AppealStatus,
        new: AppealStatus,
    ) -> bool:
        """CAS the status only if it currently equals ``expected``."""
        self.cas_calls.append((appeal_id, expected, new))
        appeal = await self.load(appeal_id)
        if appeal.status is not expected:
            return False
        import dataclasses

        self._appeals[appeal_id] = dataclasses.replace(appeal, status=new)
        return True

    async def append_event(self, appeal_id: str, event: AppealEventRecord) -> None:
        """Append one timeline event (rejects a duplicate seq)."""
        if appeal_id not in self._appeals:
            raise AppealNotFound(appeal_id)
        log = self._events.setdefault(appeal_id, [])
        if any(e.seq == event.seq for e in log):
            raise ValueError(f"duplicate seq {event.seq} for {appeal_id}")
        log.append(event)

    def events_for(self, appeal_id: str) -> List[AppealEventRecord]:
        """Return the recorded events for ``appeal_id`` (test helper)."""
        return list(self._events.get(appeal_id, []))


class FakeAudit:
    """Audit-chain fake with a settable ``verify_chain`` verdict."""

    def __init__(self, *, intact: bool = True) -> None:
        """Store the chain verdict and an append counter."""
        self.intact = intact
        self.records: List[AuditRecord] = []

    def append(self, record: AuditRecord) -> str:
        """Append a record and return its audit id."""
        self.records.append(record)
        return f"audit-{len(self.records)}"

    def verify_chain(self, appeal_id: Optional[str] = None) -> bool:
        """Return the configured chain-intact verdict."""
        return self.intact

    def iter(self, appeal_id: str) -> List[AuditRecord]:
        """Return this appeal's records (filter by attribution)."""
        return [r for r in self.records if r.appeal_id == appeal_id]


class FakeSignature:
    """Signature fake that accepts only a fixed valid token."""

    VALID = "valid-signature"

    def sign(self, appeal_hash: bytes, nurse_identity: str) -> Signature:
        """Return a deterministic signature over the hash."""
        return Signature(
            signature_b64=self.VALID,
            public_key_id="key-1",
            nurse_identity=nurse_identity,
            signed_at_iso="2026-06-07T12:00:00+00:00",
        )

    def verify(self, appeal_hash: bytes, signature: Signature) -> bool:
        """Return ``True`` only for the fixed valid signature token."""
        return signature.signature_b64 == self.VALID


class FakeAuth:
    """Auth fake: a token map for authn and ownership+role RBAC for authz."""

    def __init__(self, principals: Optional[Dict[str, Principal]] = None) -> None:
        """Store the token -> principal map."""
        self._principals = principals or {}

    def authenticate(self, token: str) -> Principal:
        """Resolve ``token`` to a principal or raise ``Unauthenticated``."""
        principal = self._principals.get(token)
        if principal is None:
            raise Unauthenticated("invalid token")
        return principal

    def authorize(self, principal: Principal, request: AuthorizationRequest) -> None:
        """Allow admins always; otherwise require per-resource ownership."""
        if principal.role == "admin":
            return
        if request.resource_id in principal.owned_ids:
            return
        raise Forbidden(
            f"{principal.role} may not {request.action} {request.resource}"
        )


class FakeTransport:
    """Voice-transport fake: tracks open channels and mints fake tokens."""

    def __init__(self, open_calls: Optional[List[str]] = None) -> None:
        """Track open channels and the bridge tokens minted."""
        self._open: Dict[str, Channel] = {}
        self.bridge_calls: List[Tuple[str, str]] = []

    async def open_channel(
        self,
        call_id: str,
        *,
        max_participants: int = 4,
        ttl: object = None,
        metadata: object = None,
    ) -> Channel:
        """Open a room for ``call_id`` and record it as live."""
        channel = Channel(
            call_id=call_id,
            room_name=f"room-{call_id}",
            agent_token=f"agent-{call_id}",
            expires_at=datetime(2026, 6, 7, 12, 15, 0, tzinfo=timezone.utc),
        )
        self._open[call_id] = channel
        return channel

    def mint_join_token(self, room_name: str, identity: str, **kw: object) -> JoinToken:
        """Mint a fixed join token (pure, no I/O)."""
        return JoinToken(
            token=f"join-{room_name}-{identity}",
            identity=identity,
            room_name=room_name,
            expires_at=datetime(2026, 6, 7, 12, 5, 0, tzinfo=timezone.utc),
        )

    async def bridge_nurse(
        self, call_id: str, nurse_identity: str, *, ttl: object = None
    ) -> JoinToken:
        """Mint a short-TTL barge-in token for an open call."""
        if call_id not in self._open:
            raise ChannelNotFound(call_id)
        self.bridge_calls.append((call_id, nurse_identity))
        return self.mint_join_token(f"room-{call_id}", nurse_identity)

    async def list_participants(self, call_id: str) -> List[Participant]:
        """List participants for an open call (empty for the fake)."""
        if call_id not in self._open:
            raise ChannelNotFound(call_id)
        return []

    async def remove_participant(self, call_id: str, identity: str) -> None:
        """Eject a participant from an open call."""
        if call_id not in self._open:
            raise ChannelNotFound(call_id)

    async def close_channel(self, call_id: str) -> None:
        """Tear down a call's room (idempotent)."""
        self._open.pop(call_id, None)

    def verify_token(self, token: str) -> Claims:
        """Decode a fake token into claims (never used in service tests)."""
        return Claims(
            issuer="fake",
            subject=token,
            room_name="room",
            expires_at=datetime(2026, 6, 7, 12, 5, 0, tzinfo=timezone.utc),
            can_publish=True,
            can_subscribe=True,
        )

    async def aclose(self) -> None:
        """No-op close for the in-process transport."""


class FakeParser:
    """Denial-parser fake with a declared supported-kind set.

    The "real" parser is constructed with the image kinds it supports and
    declines EDI via ``supports``; the "sim" parser supports every kind. Each
    records its parse calls so the fallback path can be asserted.
    """

    def __init__(self, *, supported: Optional[Tuple[ArtifactKind, ...]]) -> None:
        """Build a parser supporting ``supported`` kinds (``None`` = all)."""
        self._supported = supported
        self.parse_calls: List[ParseRequest] = []

    def supports(self, kind: ArtifactKind) -> bool:
        """Return whether this parser handles ``kind``."""
        return self._supported is None or kind in self._supported

    async def parse(self, req: ParseRequest) -> DenialExtraction:
        """Record the call and return a deterministic extraction."""
        self.parse_calls.append(req)
        field = ExtractedField(
            name="carc_code",
            value="197",
            confidence=0.95,
            provenance={"engine": "fake"},
        )
        return DenialExtraction(
            kind=req.kind,
            fields=(field,),
            overall_confidence=0.95,
            needs_human_review=False,
        )


def make_observation(complexity: float = 0.5) -> TurnObservation:
    """Build a minimal 12-dim telemetry observation for a turn."""
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
        complexity=complexity,
        ctx_tokens=256,
    )


def make_appeal(
    appeal_id: str = "appeal-1",
    *,
    status: AppealStatus = AppealStatus.DENIED,
    recoverable_cents: int = 50_000,
    sol_iso: str = "2026-07-07",
    payer_id: str = "payer-1",
    denial_code: str = "197",
    needs_human_review: bool = False,
) -> Appeal:
    """Build a minimal, valid :class:`Appeal` aggregate for service tests."""
    from backstop.domain.models import Denial, Payer
    from backstop.domain.money import Money, RecoverableDollars, SolDeadline

    payer = Payer(payer_id=payer_id, name="Acme Health")
    denial = Denial(
        denial_id=f"denial-{appeal_id}",
        payer=payer,
        plan="PPO",
        member_id_hash="hash-member",
        claim_number_hash="hash-claim",
        denial_code=denial_code,
        rarc=None,
        cpt=("99213",),
        billed=Money(cents=recoverable_cents),
        dos=date(2026, 6, 1),
    )
    return Appeal(
        id=appeal_id,
        status=status,
        denial=denial,
        recoverable=RecoverableDollars(Money(cents=recoverable_cents)),
        sol=SolDeadline.from_iso(sol_iso),
        route=None,
        needs_human_review=needs_human_review,
        events=(),
    )
