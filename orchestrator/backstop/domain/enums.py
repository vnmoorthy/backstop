"""Shared domain enumerations for the Backstop appeals kernel.

This module is part of the pure domain layer: it imports nothing outside the
standard library and carries no I/O, no vendor dependencies, and no behaviour
beyond the closed sets of values that the rest of the system pivots on. Every
enum here is a string-valued :class:`enum.Enum` so that values serialize
stably (audit logs, event payloads, JSON DTOs) without depending on member
ordinal positions.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "AppealStatus",
    "RouteDecision",
    "ModelTier",
    "ArtifactKind",
    "Speaker",
    "SpecialistKind",
    "IntegrationMode",
    "DialogAct",
]


class AppealStatus(str, Enum):
    """Lifecycle state of an :class:`~backstop.domain.models.Appeal`.

    The legal transition graph is enforced by
    :mod:`backstop.domain.appeal_aggregate`; this enum only names the states::

        DENIED -> TRIAGED -> IN_APPEAL -> AWAITING_SIGNOFF -> FILED
        (any non-terminal state may also move to ABANDONED)
    """

    DENIED = "DENIED"
    TRIAGED = "TRIAGED"
    IN_APPEAL = "IN_APPEAL"
    AWAITING_SIGNOFF = "AWAITING_SIGNOFF"
    FILED = "FILED"
    ABANDONED = "ABANDONED"


class RouteDecision(str, Enum):
    """Disposition chosen for a denial by the routing policy.

    Drives whether the swarm re-submits, drafts an appeal, requests a
    peer-to-peer review, or writes the balance off as unrecoverable.
    """

    RESUBMIT = "RESUBMIT"
    APPEAL = "APPEAL"
    PEER_TO_PEER = "PEER_TO_PEER"
    WRITE_OFF = "WRITE_OFF"


class ModelTier(str, Enum):
    """Compute/quality tier selected per turn by the adaptive router.

    ``CLOUD_PREMIUM`` favours quality, ``ONDEVICE_FAST`` favours latency and
    privacy, and ``HYBRID_BALANCED`` splits the difference.
    """

    CLOUD_PREMIUM = "CLOUD_PREMIUM"
    HYBRID_BALANCED = "HYBRID_BALANCED"
    ONDEVICE_FAST = "ONDEVICE_FAST"


class ArtifactKind(str, Enum):
    """Declared type of an ingested denial/claim artifact.

    The declared kind selects the parser and is supplied by the caller; no
    server-side path is ever derived from it.
    """

    EOB = "EOB"
    CMS1500 = "CMS1500"
    UB04 = "UB04"
    X12_835 = "X12_835"
    X12_837 = "X12_837"
    X12_277 = "X12_277"
    PDF_IMAGE = "PDF_IMAGE"


class Speaker(str, Enum):
    """Originator of a turn in a payer phone call.

    ``AGENT`` is the Backstop swarm, ``REP`` is the human payer representative,
    and ``IVR`` is the automated phone tree.
    """

    AGENT = "AGENT"
    REP = "REP"
    IVR = "IVR"


class SpecialistKind(str, Enum):
    """Category of payer-side desk a specialist sub-agent is calling."""

    PROVIDER_LINE = "PROVIDER_LINE"
    BILLING_OFFICE = "BILLING_OFFICE"
    RECORDS_DESK = "RECORDS_DESK"
    PRIOR_AUTH_DESK = "PRIOR_AUTH_DESK"


class IntegrationMode(str, Enum):
    """Whether a port is backed by a live vendor integration or a local fallback.

    Surfaced on the dashboard so a ``sim`` adapter is never misrepresented as
    live (the honesty contract).
    """

    REAL = "real"
    SIM = "sim"


class DialogAct(str, Enum):
    """Speech-act of a composed agent line in a payer call.

    Lets downstream policy/telemetry reason about call flow without parsing the
    free text of the line.
    """

    GREETING = "GREETING"
    IDENTIFY = "IDENTIFY"
    STATE_PURPOSE = "STATE_PURPOSE"
    PROVIDE_INFO = "PROVIDE_INFO"
    REQUEST_INFO = "REQUEST_INFO"
    CITE_POLICY = "CITE_POLICY"
    REBUT = "REBUT"
    CONFIRM = "CONFIRM"
    ESCALATE = "ESCALATE"
    CLOSE = "CLOSE"
