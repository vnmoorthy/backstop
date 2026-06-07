"""The immutable :class:`Container` — all ports + services, no logic.

The composition root builds exactly one frozen ``Container`` and stores it on
``app.state``. Controllers read it; they construct nothing. Every slot is typed
``Optional`` so the empty shell type-checks today (M0.3) and is fully populated
by ``composition.wiring.build_container`` once the adapters/services land.

To keep this file dependency-free of every port/service module at runtime (the
container holds *instances*, it must not import their definitions to stay a pure
data shell), the typed slots are declared under ``TYPE_CHECKING`` only. At
runtime the fields are plain ``Optional`` attributes defaulting to ``None``;
static tooling sees the precise port/service protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings
    from backstop.ports.appeal_repository_port import AppealRepositoryPort
    from backstop.ports.audit_log_port import AuditLogPort
    from backstop.ports.auth_port import AuthPort
    from backstop.ports.clock_port import ClockPort
    from backstop.ports.concurrency_gate_port import ConcurrencyGatePort
    from backstop.ports.cost_ledger_port import CostLedgerPort
    from backstop.ports.denial_parser_port import DenialParserPort
    from backstop.ports.event_bus_port import EventBusPort
    from backstop.ports.file_store_port import FileStorePort
    from backstop.ports.id_gen_port import IdGenPort
    from backstop.ports.ivr_port import IvrPort
    from backstop.ports.letter_render_port import LetterRenderPort
    from backstop.ports.llm_gateway_port import LLMGatewayPort
    from backstop.ports.reasoning_port import ReasoningPort
    from backstop.ports.redaction_port import RedactionPort
    from backstop.ports.retrieval_port import RetrievalPort
    from backstop.ports.routing_port import RoutingPort
    from backstop.ports.signature_port import SignaturePort
    from backstop.ports.speech_synthesis_port import SpeechSynthesisPort
    from backstop.ports.task_supervisor_port import TaskSupervisorPort
    from backstop.ports.voice_transport_port import VoiceTransportPort


@dataclass(frozen=True)
class Container:
    """Immutable holder of every wired port + service.

    Frozen: reassigning any field raises ``dataclasses.FrozenInstanceError``.
    Holds no logic — purely the object graph built by the composition root.
    """

    settings: Optional[Settings] = None

    # ── cross-cutting singletons ──────────────────────────────────────
    clock: Optional[ClockPort] = None
    id_gen: Optional[IdGenPort] = None
    redaction: Optional[RedactionPort] = None
    audit: Optional[AuditLogPort] = None
    cost: Optional[CostLedgerPort] = None
    tasks: Optional[TaskSupervisorPort] = None

    # ── capability ports ───────────────────────────────────────────
    routing: Optional[RoutingPort] = None
    retrieval: Optional[RetrievalPort] = None
    gateway: Optional[LLMGatewayPort] = None
    parser: Optional[DenialParserPort] = None
    reasoning: Optional[ReasoningPort] = None
    speech: Optional[SpeechSynthesisPort] = None
    transport: Optional[VoiceTransportPort] = None
    gate: Optional[ConcurrencyGatePort] = None
    ivr: Optional[IvrPort] = None
    repo: Optional[AppealRepositoryPort] = None
    files: Optional[FileStorePort] = None
    events: Optional[EventBusPort] = None
    letters: Optional[LetterRenderPort] = None
    signature: Optional[SignaturePort] = None
    auth: Optional[AuthPort] = None

    # ── services (use-cases) ───────────────────────────────────────
    # Service slots are added by the Services + Integration milestones (M16/M17)
    # once the service modules exist. The foundation holds the port graph only,
    # so this skeleton type-checks without forward-referencing unbuilt modules.
