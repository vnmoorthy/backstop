"""Per-port ``make_<port>(settings, shared)`` factories (stubs).

Each factory selects the real-vs-sim adapter by ``settings.mode_for(port)`` and
returns a process-wide singleton. PAVO is the one exception: it never branches on
"quality" — it tries the torch backend and falls back to the bit-faithful numpy
backend, both running the same frozen policy.

Every factory here raises :class:`NotImplementedError`; the real adapter
construction is wired in WS-Integration. The signatures are stable so
``composition.wiring.build_container`` can be written against them now.

``shared`` is the bag of already-constructed cross-cutting singletons
(redaction/audit/cost/clock/id_gen, the shared HTTP client, the DB handle, the
runbook corpus) that several adapters depend on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

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

# Bag of pre-built shared singletons passed to factories that need them.
Shared = Mapping[str, Any]


def make_clock(settings: Settings, shared: Shared) -> ClockPort:
    """Build the :class:`ClockPort` singleton."""
    raise NotImplementedError("make_clock is implemented in WS-Integration")


def make_id_gen(settings: Settings, shared: Shared) -> IdGenPort:
    """Build the :class:`IdGenPort` singleton."""
    raise NotImplementedError("make_id_gen is implemented in WS-Integration")


def make_task_supervisor(settings: Settings, shared: Shared) -> TaskSupervisorPort:
    """Build the :class:`TaskSupervisorPort` singleton."""
    raise NotImplementedError("make_task_supervisor is implemented in WS-Integration")


def make_redaction(settings: Settings, shared: Shared) -> RedactionPort:
    """Build the :class:`RedactionPort` — sole producer of ``RedactedText``."""
    raise NotImplementedError("make_redaction is implemented in WS-Integration")


def make_audit(settings: Settings, shared: Shared) -> AuditLogPort:
    """Build the hash-chained :class:`AuditLogPort` singleton."""
    raise NotImplementedError("make_audit is implemented in WS-Integration")


def make_cost(settings: Settings, shared: Shared) -> CostLedgerPort:
    """Build the priced :class:`CostLedgerPort` singleton."""
    raise NotImplementedError("make_cost is implemented in WS-Integration")


def make_routing(settings: Settings, shared: Shared) -> RoutingPort:
    """Build the PAVO :class:`RoutingPort` (torch backend, numpy fallback)."""
    raise NotImplementedError("make_routing is implemented in WS-Integration")


def make_retrieval(settings: Settings, shared: Shared) -> RetrievalPort:
    """Build the Moss :class:`RetrievalPort` (real HTTP vs sim TF-IDF)."""
    raise NotImplementedError("make_retrieval is implemented in WS-Integration")


def make_gateway(settings: Settings, shared: Shared) -> LLMGatewayPort:
    """Build the TrueFoundry :class:`LLMGatewayPort` (the single chokepoint)."""
    raise NotImplementedError("make_gateway is implemented in WS-Integration")


def make_parser(settings: Settings, shared: Shared) -> DenialParserPort:
    """Build the Unsiloed :class:`DenialParserPort` (real bytes vs sim X12)."""
    raise NotImplementedError("make_parser is implemented in WS-Integration")


def make_reasoning(settings: Settings, shared: Shared) -> ReasoningPort:
    """Build the MiniMax :class:`ReasoningPort` (real vs sim grounded-NLG)."""
    raise NotImplementedError("make_reasoning is implemented in WS-Integration")


def make_speech(settings: Settings, shared: Shared) -> SpeechSynthesisPort:
    """Build the Qwen :class:`SpeechSynthesisPort` (real DashScope vs sim DSP)."""
    raise NotImplementedError("make_speech is implemented in WS-Integration")


def make_transport(settings: Settings, shared: Shared) -> VoiceTransportPort:
    """Build the LiveKit :class:`VoiceTransportPort` (real vs in-process sim)."""
    raise NotImplementedError("make_transport is implemented in WS-Integration")


def make_gate(settings: Settings, shared: Shared) -> ConcurrencyGatePort:
    """Build the AWS :class:`ConcurrencyGatePort` (Fargate vs semaphore)."""
    raise NotImplementedError("make_gate is implemented in WS-Integration")


def make_ivr(settings: Settings, shared: Shared) -> IvrPort:
    """Build the sim-only :class:`IvrPort` (honesty contract: never a real call)."""
    raise NotImplementedError("make_ivr is implemented in WS-Integration")


def make_repo(settings: Settings, shared: Shared) -> AppealRepositoryPort:
    """Build the :class:`AppealRepositoryPort` (SQLite vs bounded-memory)."""
    raise NotImplementedError("make_repo is implemented in WS-Integration")


def make_file_store(settings: Settings, shared: Shared) -> FileStorePort:
    """Build the :class:`FileStorePort` (S3 vs path-jailed local)."""
    raise NotImplementedError("make_file_store is implemented in WS-Integration")


def make_event_bus(settings: Settings, shared: Shared) -> EventBusPort:
    """Build the RedactedText-only :class:`EventBusPort` singleton."""
    raise NotImplementedError("make_event_bus is implemented in WS-Integration")


def make_letter_render(settings: Settings, shared: Shared) -> LetterRenderPort:
    """Build the markup-escaping :class:`LetterRenderPort` singleton."""
    raise NotImplementedError("make_letter_render is implemented in WS-Integration")


def make_signature(settings: Settings, shared: Shared) -> SignaturePort:
    """Build the Ed25519 :class:`SignaturePort` singleton."""
    raise NotImplementedError("make_signature is implemented in WS-Integration")


def make_auth(settings: Settings, shared: Shared) -> AuthPort:
    """Build the JWT/RBAC :class:`AuthPort` singleton."""
    raise NotImplementedError("make_auth is implemented in WS-Integration")
