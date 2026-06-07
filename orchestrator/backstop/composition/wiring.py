"""Composition root: ``build_container(settings)``.

The ONE impure graph. It constructs the shared cross-cutting singletons first
(redaction, audit, cost, clock, id-gen, task supervisor, the shared HTTP client,
the DB handle, the runbook corpus, the CARC table), then builds every capability
adapter by mode via ``adapter_factory``, then assembles the twelve services
(which depend only on ports), and finally returns the immutable
:class:`Container`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Tuple

from backstop.composition import adapter_factory as factory
from backstop.composition.container import Container

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backstop.infra.config import Settings
    from backstop.ports.denial_parser_port import ParseRequest

__all__ = ["build_container", "default_batch_splitter"]


def default_batch_splitter(content: bytes) -> Sequence[Tuple[str, ParseRequest]]:
    """Split a batch payload into one ``(claim_id, ParseRequest)`` per artifact.

    The default treats the whole upload as a single EOB artifact. A real batch
    deployment injects a payer-specific splitter; this keeps the composition
    root honest (one pure default) without baking EDI segmentation into a
    service.
    """
    from backstop.domain.enums import ArtifactKind
    from backstop.ports.denial_parser_port import ParseRequest

    return [("claim-1", ParseRequest(content=content, kind=ArtifactKind.EOB))]


def build_container(settings: Settings) -> Container:
    """Build the fully-wired immutable :class:`Container` from ``settings``.

    Ordering:

    1. Shared singletons: ``clock``, ``id_gen``, the HTTP client, the DB handle,
       the runbook corpus, the CARC table, ``redaction`` (sole ``RedactedText``
       producer), ``audit``, ``cost``, ``tasks``.
    2. Capability ports via ``adapter_factory.make_<port>(settings, shared)``,
       selecting real-vs-sim by ``settings.mode_for(port)``; PAVO tries torch
       then numpy.
    3. The twelve services, each receiving only ports.
    4. Assemble and freeze into a :class:`Container`.

    Args:
        settings: The frozen application settings.

    Returns:
        The immutable wired container.
    """
    # ── 1. shared cross-cutting singletons ────────────────────────────── #
    from backstop.adapters.text.runbook_corpus import RunbookCorpus
    from backstop.domain.carc_table import load_carc_table
    from backstop.infra.db import make_db
    from backstop.infra.http_client import make_http_client

    shared: Dict[str, Any] = {}
    http_clients: List[Any] = []
    shared["settings"] = settings
    shared["clock"] = factory.make_clock(settings, shared)
    shared["id_gen"] = factory.make_id_gen(settings, shared)
    shared["tasks"] = factory.make_task_supervisor(settings, shared)
    shared["db"] = make_db(settings)
    # Every httpx client the real adapters use is registered here so the
    # lifespan can close them all on shutdown. Clients are created lazily by the
    # factories (only in real mode) so a sim graph opens no transport at all.
    shared["http_clients"] = http_clients

    def _shared_http() -> Any:
        if "http" not in shared:
            shared["http"] = make_http_client(settings)
            http_clients.append(shared["http"])
        return shared["http"]

    shared["make_shared_http"] = _shared_http
    shared["corpus"] = RunbookCorpus.from_dir()
    shared["carc_table"] = load_carc_table()
    shared["redaction"] = factory.make_redaction(settings, shared)
    shared["audit"] = factory.make_audit(settings, shared)
    shared["cost"] = factory.make_cost(settings, shared)

    # ── 2. capability ports (real vs sim by mode) ─────────────────────── #
    routing = factory.make_routing(settings, shared)
    retrieval = factory.make_retrieval(settings, shared)
    gateway = factory.make_gateway(settings, shared)
    parser = factory.make_parser(settings, shared)
    parser_fallback = factory.make_parser_fallback(settings, shared)
    reasoning = factory.make_reasoning(settings, shared)
    speech = factory.make_speech(settings, shared)
    transport = factory.make_transport(settings, shared)
    gate = factory.make_gate(settings, shared)
    ivr = factory.make_ivr(settings, shared)
    repo = factory.make_repo(settings, shared)
    files = factory.make_file_store(settings, shared)
    events = factory.make_event_bus(settings, shared)
    letters = factory.make_letter_render(settings, shared)
    signature = factory.make_signature(settings, shared)
    auth = factory.make_auth(settings, shared)

    clock = shared["clock"]
    id_gen = shared["id_gen"]
    redaction = shared["redaction"]
    audit = shared["audit"]
    cost = shared["cost"]
    tasks = shared["tasks"]

    # ── 3. services (constructor injection; ports only) ───────────────── #
    from backstop.services.appeal_service import AppealService
    from backstop.services.auth_service import AuthService
    from backstop.services.call_service import CallService
    from backstop.services.ingest_denial_service import IngestDenialService
    from backstop.services.ingestion_batch_service import IngestionBatchService
    from backstop.services.letter_service import LetterService
    from backstop.services.nurse_bridge_service import NurseBridgeService
    from backstop.services.reconcile_service import ReconcileService
    from backstop.services.review_service import ReviewService
    from backstop.services.signoff_service import SignoffService
    from backstop.services.swarm_service import SwarmService
    from backstop.services.triage_service import TriageService

    appeal_service = AppealService(
        repo=repo,
        clock=clock,
        id_gen=id_gen,
        carc_table=shared["carc_table"],
    )
    call_service = CallService(
        routing=routing,
        retrieval=retrieval,
        reasoning=reasoning,
        speech=speech,
        redaction=redaction,
        events=events,
        clock=clock,
    )
    swarm_service = SwarmService(gate=gate)
    reconcile_service = ReconcileService()
    ingest_service = IngestDenialService(
        gate=gate,
        primary_parser=parser,
        fallback_parser=parser_fallback,
        audit=audit,
    )
    ingestion_batch_service = IngestionBatchService(
        gate=gate,
        ingest=ingest_service,
        splitter=default_batch_splitter,
    )
    triage_service = TriageService(repo, clock)
    letter_service = LetterService(
        redaction=redaction,
        renderer=letters,
        files=files,
        ttl_seconds=settings.file_ttl_s,
    )
    review_service = ReviewService(repo, redaction)
    signoff_service = SignoffService(
        repo=repo,
        audit=audit,
        signature=signature,
        clock=clock,
    )
    nurse_bridge_service = NurseBridgeService(transport=transport, auth=auth)
    auth_service = AuthService(auth)

    # ── 4. assemble and freeze ────────────────────────────────────────── #
    return Container(
        settings=settings,
        clock=clock,
        id_gen=id_gen,
        redaction=redaction,
        audit=audit,
        cost=cost,
        tasks=tasks,
        routing=routing,
        retrieval=retrieval,
        gateway=gateway,
        parser=parser,
        reasoning=reasoning,
        speech=speech,
        transport=transport,
        gate=gate,
        ivr=ivr,
        repo=repo,
        files=files,
        events=events,
        letters=letters,
        signature=signature,
        auth=auth,
        appeal_service=appeal_service,
        swarm_service=swarm_service,
        call_service=call_service,
        reconcile_service=reconcile_service,
        ingest_service=ingest_service,
        ingestion_batch_service=ingestion_batch_service,
        triage_service=triage_service,
        letter_service=letter_service,
        review_service=review_service,
        signoff_service=signoff_service,
        nurse_bridge_service=nurse_bridge_service,
        auth_service=auth_service,
        http_clients=tuple(http_clients),
    )
