"""The agent roster: 22 specialized agents that work a denied claim end to end.

Each :class:`Agent` owns exactly one job. The swarm activates them across nine
phases (intake -> triage -> route -> call -> reason -> draft -> file -> recover
-> learn). Every sponsor technology is load-bearing somewhere in the roster.

This is the product framing competitors like Thoughtful AI use (named agents),
applied to denial *recovery* — and it is real: most agents map to a built
service/adapter; the rest are the roadmap to the closed loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

# Ordered lifecycle phases the swarm moves through.
PHASES: List[str] = [
    "intake",
    "triage",
    "route",
    "call",
    "reason",
    "draft",
    "file",
    "recover",
    "learn",
]


@dataclass(frozen=True)
class Agent:
    """One specialized agent: a single job, a phase, and the tech that powers it."""

    id: str
    name: str
    role: str
    phase: str
    sponsor: str = ""
    built: bool = False  # True if a real service/adapter backs it today


ROSTER: List[Agent] = [
    # ---- intake -------------------------------------------------------------
    Agent("intake", "Intake Agent", "Parses 835 / EOB / 277 into canonical denials", "intake", "Unsiloed", True),
    Agent("eob_ocr", "Document Agent", "OCRs scanned EOBs and denial letters", "intake", "Unsiloed", False),
    # ---- triage -------------------------------------------------------------
    Agent("triage", "Triage Agent", "Scores recoverable-$ x filing deadline; builds the worklist", "triage", "PAVO", True),
    Agent("disposition", "Disposition Agent", "Routes resubmit vs appeal vs peer-to-peer vs write-off", "triage", "MiniMax", True),
    # ---- route / prep -------------------------------------------------------
    Agent("router", "PAVO Router", "Routes each task to the cheapest viable model tier", "route", "PAVO", True),
    Agent("eligibility", "Eligibility Agent", "Re-verifies coverage and benefits", "route", "", False),
    Agent("priorauth", "Prior-Auth Agent", "Locates and validates the authorization on file", "route", "Moss", True),
    Agent("records", "Records Agent", "Pulls the clinical evidence and chart notes", "route", "Moss", True),
    Agent("coding", "Coding Agent", "Checks CPT / modifier / ICD for a correctable error", "route", "MiniMax", True),
    Agent("supervisor", "Supervisor Agent", "Orchestrates the swarm; escalates to the human nurse", "route", "AWS", True),
    # ---- call (the voice swarm) --------------------------------------------
    Agent("provider_line", "Provider-Line Caller", "Calls the payer provider-services line", "call", "LiveKit", True),
    Agent("billing_office", "Billing-Office Caller", "Calls the billing office in parallel", "call", "LiveKit", True),
    Agent("records_desk", "Records-Desk Caller", "Calls the records desk in parallel", "call", "LiveKit", True),
    Agent("ivr", "IVR Navigator", "Navigates phone trees and survives holds", "call", "MiniMax", True),
    Agent("voice", "Voice Agent", "One cloned brand voice for every call", "call", "Qwen", True),
    # ---- reason -------------------------------------------------------------
    Agent("rebuttal", "Rebuttal Retrieval", "Retrieves the winning rebuttal mid-call", "reason", "Moss", True),
    Agent("reconciler", "Reconciler Agent", "Finds the contradiction that overturns the denial", "reason", "", True),
    # ---- draft --------------------------------------------------------------
    Agent("letter", "Letter Writer", "Drafts the verbatim, audit-grade appeal letter", "draft", "MiniMax", True),
    Agent("compliance", "Compliance Agent", "Redacts PHI and writes the tamper-evident audit", "draft", "TrueFoundry", True),
    # ---- file ---------------------------------------------------------------
    Agent("filing", "Filing Agent", "Submits via payer portal / fax / clearinghouse", "file", "", False),
    # ---- recover ------------------------------------------------------------
    Agent("status", "Status Tracker", "Polls 277 / remit for the outcome", "recover", "", False),
    Agent("recovery", "Recovery & Billing", "Computes recovered $ and the contingency invoice", "recover", "", True),
    # ---- learn --------------------------------------------------------------
    Agent("learning", "Learning Agent", "Updates the win-corpus + PAVO priors from every outcome", "learn", "PAVO", False),
]


def by_phase(phase: str) -> List[Agent]:
    """Return every agent that works in *phase*."""
    return [a for a in ROSTER if a.phase == phase]


def sponsors_used() -> List[str]:
    """Distinct sponsor technologies represented across the roster."""
    return sorted({a.sponsor for a in ROSTER if a.sponsor})


def built_count() -> int:
    """How many agents are backed by a real service/adapter today."""
    return sum(1 for a in ROSTER if a.built)
