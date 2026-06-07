"""Per-port ``make_<port>(settings, shared)`` factories.

Each factory selects the real-vs-sim adapter by ``settings.mode_for(port)`` and
returns a process-wide singleton. PAVO is the one exception: it never branches on
"quality" — it tries the torch backend and falls back to the bit-faithful numpy
backend, both running the same frozen policy.

``shared`` is the bag of already-constructed cross-cutting singletons
(redaction/audit/cost/clock/id_gen, the shared HTTP client, the DB handle, the
runbook corpus, the CARC table) that several adapters depend on. The composition
root (``composition.wiring.build_container``) builds those first, then calls
these factories in dependency order.

The factories live in the composition layer, which is permitted to import every
adapter; the vendor-SDK imports inside those adapters are themselves lazy, so
constructing a *sim* graph never imports torch/boto3/livekit.
"""

from __future__ import annotations

from pathlib import Path
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


def _is_real(settings: Settings, port: str) -> bool:
    """Return ``True`` when ``port`` is configured for the real adapter."""
    return settings.mode_for(port) == "real"


# ── cross-cutting singletons ──────────────────────────────────────────── #
def make_clock(settings: Settings, shared: Shared) -> ClockPort:
    """Build the :class:`ClockPort` singleton (system wall clock)."""
    from backstop.adapters.system.system_clock_adapter import SystemClockAdapter

    return SystemClockAdapter()


def make_id_gen(settings: Settings, shared: Shared) -> IdGenPort:
    """Build the :class:`IdGenPort` singleton (UUID4-backed)."""
    from backstop.adapters.system.uuid_id_gen_adapter import UuidIdGenAdapter

    return UuidIdGenAdapter()


def make_task_supervisor(settings: Settings, shared: Shared) -> TaskSupervisorPort:
    """Build the :class:`TaskSupervisorPort` singleton (asyncio-tracked)."""
    from backstop.adapters.system.asyncio_task_supervisor import AsyncioTaskSupervisor

    return AsyncioTaskSupervisor()


def make_redaction(settings: Settings, shared: Shared) -> RedactionPort:
    """Build the :class:`RedactionPort` — sole producer of ``RedactedText``.

    Real mode layers the TrueFoundry redaction service over the deterministic
    local rules; sim mode uses the local rules alone. Either way the local
    adapter is the floor, so PHI scrubbing never silently disappears.
    """
    from backstop.adapters.truefoundry.local_redaction_adapter import LocalRedactionAdapter

    local = LocalRedactionAdapter()
    if _is_real(settings, "redaction"):
        from backstop.adapters.truefoundry.tf_redaction_adapter import (
            TrueFoundryRedactionAdapter,
        )

        return TrueFoundryRedactionAdapter(
            local=local,
            api_key=settings.truefoundry_api_key,
            base_url=settings.truefoundry_base_url,
        )
    return local


def make_audit(settings: Settings, shared: Shared) -> AuditLogPort:
    """Build the hash-chained :class:`AuditLogPort` singleton (SQLite/memory)."""
    from backstop.adapters.truefoundry.hashchain_audit_adapter import HashChainAuditAdapter

    db_path = ":memory:" if not _is_real(settings, "audit") else shared["db"].sqlite_path
    return HashChainAuditAdapter(db_path=db_path)


def make_cost(settings: Settings, shared: Shared) -> CostLedgerPort:
    """Build the priced :class:`CostLedgerPort` singleton."""
    from backstop.adapters.truefoundry.cost_ledger_adapter import CostLedgerAdapter

    return CostLedgerAdapter()


# ── capability ports ──────────────────────────────────────────────────── #
def make_routing(settings: Settings, shared: Shared) -> RoutingPort:
    """Build the PAVO :class:`RoutingPort` (torch backend, numpy fallback).

    Never branches on a quality "mode": the policy is identical. It honours the
    ``PAVO_ADAPTER_IMPL`` preference, then falls back to the bit-faithful numpy
    backend if torch is unavailable or the checkpoint fails to load — a single
    router singleton either way.
    """
    import logging

    weights_path = settings.pavo_weights_path
    npz_path = settings.pavo_weights_npz
    prefer_torch = settings.pavo_adapter_impl.strip().lower() == "torch"

    if prefer_torch:
        try:
            from backstop.adapters.pavo.torch_routing_adapter import TorchPavoRoutingAdapter

            return TorchPavoRoutingAdapter(
                weights_path=weights_path,
                device=settings.pavo_device,
            )
        except Exception as exc:  # torch missing / bad checkpoint -> numpy fallback
            logging.getLogger(__name__).info(
                "PAVO torch backend unavailable, falling back to numpy: %s", exc
            )

    from backstop.adapters.pavo.numpy_routing_adapter import NumpyPavoRoutingAdapter

    return NumpyPavoRoutingAdapter(weights_path=npz_path)


def _host_client(settings: Settings, shared: Shared, base_url: str) -> Any:
    """Make a host-bound httpx client and register it for shutdown close."""
    from backstop.infra.http_client import make_http_client

    client = make_http_client(settings, base_url=base_url)
    shared["http_clients"].append(client)
    return client


def make_retrieval(settings: Settings, shared: Shared) -> RetrievalPort:
    """Build the Moss :class:`RetrievalPort` (real HTTP vs sim TF-IDF)."""
    if _is_real(settings, "retrieval"):
        from backstop.adapters.moss.moss_http_adapter import MossHttpAdapter

        return MossHttpAdapter(
            _host_client(settings, shared, settings.moss_base_url),
            project_id=settings.moss_project_id or "",
            project_key=settings.moss_project_key or "",
        )

    from backstop.adapters.moss.tfidf_retrieval_adapter import TfidfRetrievalAdapter

    return TfidfRetrievalAdapter(shared["corpus"])


def make_gateway(settings: Settings, shared: Shared) -> LLMGatewayPort:
    """Build the TrueFoundry :class:`LLMGatewayPort` (the single chokepoint)."""
    redaction = shared["redaction"]
    audit = shared["audit"]
    cost = shared["cost"]

    if _is_real(settings, "gateway"):
        from backstop.adapters.truefoundry.tf_gateway_adapter import (
            TrueFoundryGatewayAdapter,
        )

        return TrueFoundryGatewayAdapter(
            redaction=redaction,
            audit=audit,
            cost=cost,
            api_key=settings.truefoundry_api_key,
            base_url=settings.truefoundry_base_url,
            inference_path=settings.truefoundry_inference_path,
            default_model=settings.truefoundry_default_model,
        )

    from backstop.adapters.truefoundry.local_completion_engine import LocalCompletionEngine
    from backstop.adapters.truefoundry.sim_gateway_adapter import SimGatewayAdapter

    return SimGatewayAdapter(
        redaction=redaction,
        audit=audit,
        cost=cost,
        engine=LocalCompletionEngine(corpus=shared["corpus"]),
    )


def make_parser(settings: Settings, shared: Shared) -> DenialParserPort:
    """Build the Unsiloed :class:`DenialParserPort` (real bytes vs sim X12)."""
    if _is_real(settings, "parser"):
        from backstop.adapters.unsiloed.unsiloed_http_adapter import (
            UnsiloedDenialParserAdapter,
        )

        return UnsiloedDenialParserAdapter(
            shared["make_shared_http"](),
            api_key=settings.unsiloed_api_key or "",
            base_url=settings.unsiloed_base_url,
            confidence_floor=settings.unsiloed_confidence_floor,
        )

    from backstop.adapters.unsiloed.deterministic_parser_adapter import (
        DeterministicDenialParserAdapter,
    )

    return DeterministicDenialParserAdapter(
        carc_table=shared["carc_table"],
        confidence_floor=settings.unsiloed_confidence_floor,
    )


def make_parser_fallback(settings: Settings, shared: Shared) -> DenialParserPort:
    """Build the always-available deterministic sim parser (fallback path)."""
    from backstop.adapters.unsiloed.deterministic_parser_adapter import (
        DeterministicDenialParserAdapter,
    )

    return DeterministicDenialParserAdapter(
        carc_table=shared["carc_table"],
        confidence_floor=settings.unsiloed_confidence_floor,
    )


def make_reasoning(settings: Settings, shared: Shared) -> ReasoningPort:
    """Build the MiniMax :class:`ReasoningPort` (real vs sim grounded-NLG)."""
    if _is_real(settings, "reasoning"):
        from backstop.adapters.minimax.minimax_adapter import (
            MiniMaxReasoningAdapter,
            MiniMaxSettings,
        )

        client = _host_client(settings, shared, settings.minimax_base_url)
        ms = MiniMaxSettings(
            api_key=settings.minimax_api_key or "",
            base_url=settings.minimax_base_url,
            model=settings.minimax_model,
            group_id=settings.minimax_group_id,
            route=settings.minimax_route,
        )
        return MiniMaxReasoningAdapter(http=client, settings=ms)

    from backstop.adapters.minimax.local_reasoning_adapter import LocalReasoningAdapter

    return LocalReasoningAdapter(carc_table=shared["carc_table"])


def make_speech(settings: Settings, shared: Shared) -> SpeechSynthesisPort:
    """Build the Qwen :class:`SpeechSynthesisPort` (real DashScope vs sim DSP)."""
    if _is_real(settings, "speech"):
        from backstop.adapters.qwen.qwen_tts_adapter import QwenTtsAdapter

        api_key = settings.qwen_api_key or settings.dashscope_api_key or ""
        return QwenTtsAdapter(
            api_key,
            http=shared["make_shared_http"](),
            region=settings.qwen_region,
            model=settings.qwen_tts_model,
            default_voice_id=settings.qwen_voice_id or "Cherry",
        )

    from backstop.adapters.qwen.sim_tts_adapter import SimTtsAdapter

    return SimTtsAdapter()


def make_transport(settings: Settings, shared: Shared) -> VoiceTransportPort:
    """Build the LiveKit :class:`VoiceTransportPort` (real vs in-process sim)."""
    if _is_real(settings, "transport"):
        from backstop.adapters.livekit.livekit_adapter import LiveKitTransportAdapter

        return LiveKitTransportAdapter(
            url=settings.livekit_url or "",
            api_key=settings.livekit_api_key or "",
            api_secret=settings.livekit_api_secret or "",
        )

    from backstop.adapters.livekit.inprocess_transport_adapter import (
        InProcessTransportAdapter,
    )

    return InProcessTransportAdapter(secret=settings.livekit_sim_secret)


def make_gate(settings: Settings, shared: Shared) -> ConcurrencyGatePort:
    """Build the AWS :class:`ConcurrencyGatePort` (Fargate vs semaphore)."""
    if _is_real(settings, "gate"):
        from backstop.adapters.aws.fargate_gate import (
            FargateConcurrencyGate,
            FargateGateConfig,
        )

        config = FargateGateConfig(
            cluster="backstop",
            task_definition="backstop-agent",
            subnets=[],
            security_groups=[],
            container_name="agent",
            max_concurrency=settings.max_concurrency,
        )
        return FargateConcurrencyGate(config=config, region=settings.aws_region)

    from backstop.adapters.aws.semaphore_gate import SemaphoreConcurrencyGate

    return SemaphoreConcurrencyGate(max_concurrency=settings.max_concurrency)


def make_ivr(settings: Settings, shared: Shared) -> IvrPort:
    """Build the sim-only :class:`IvrPort` (honesty contract: never a real call)."""
    from backstop.adapters.ivr.ivr_sim_adapter import IvrSimAdapter

    return IvrSimAdapter(id_gen=shared["id_gen"])


def make_repo(settings: Settings, shared: Shared) -> AppealRepositoryPort:
    """Build the :class:`AppealRepositoryPort` (SQLite vs bounded-memory).

    The repository follows the global persistence posture rather than a single
    port mode flag: ``BACKSTOP_MODE=real`` selects durable WAL SQLite, while the
    default selects the bounded LRU+TTL in-memory store.
    """
    if settings.backstop_mode == "real":
        from backstop.adapters.persistence.sqlite_appeal_repo import SqliteAppealRepo

        return SqliteAppealRepo(database_path=shared["db"].sqlite_path)

    from backstop.adapters.persistence.memory_appeal_repo import MemoryAppealRepo

    return MemoryAppealRepo(
        clock=shared["clock"],
        capacity=10_000,
        ttl_seconds=float(settings.file_ttl_s) * 24,
    )


def make_file_store(settings: Settings, shared: Shared) -> FileStorePort:
    """Build the :class:`FileStorePort` (S3 vs path-jailed local)."""
    from backstop.adapters.filestore.local_filestore_adapter import LocalFileStoreAdapter

    return LocalFileStoreAdapter(
        root=Path(settings.artifact_dir),
        secret=settings.backstop_auth_secret,
        id_gen=shared["id_gen"],
        clock=shared["clock"],
        url_ttl_seconds=settings.file_ttl_s,
    )


def make_event_bus(settings: Settings, shared: Shared) -> EventBusPort:
    """Build the RedactedText-only :class:`EventBusPort` singleton."""
    from backstop.adapters.eventbus.ws_event_bus_adapter import WsEventBusAdapter

    return WsEventBusAdapter()


def make_letter_render(settings: Settings, shared: Shared) -> LetterRenderPort:
    """Build the markup-escaping :class:`LetterRenderPort` singleton."""
    from backstop.adapters.letter.reportlab_letter_adapter import ReportlabLetterAdapter

    return ReportlabLetterAdapter()


def make_signature(settings: Settings, shared: Shared) -> SignaturePort:
    """Build the Ed25519 :class:`SignaturePort` singleton."""
    from backstop.adapters.signoff.ed25519_signature_adapter import Ed25519SignatureAdapter

    return Ed25519SignatureAdapter(clock=shared["clock"])


def make_auth(settings: Settings, shared: Shared) -> AuthPort:
    """Build the JWT/RBAC :class:`AuthPort` singleton."""
    from backstop.adapters.auth.jwt_auth_adapter import JwtAuthAdapter

    return JwtAuthAdapter(
        secret=settings.backstop_auth_secret,
        issuer=settings.backstop_auth_issuer,
        leeway_seconds=30,
    )
