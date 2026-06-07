"""IvrPort — sandbox IVR navigation (L2 port, sim-only).

Drives a deterministic DTMF state machine against a sandbox IVR: dial a specialist
line, push tones to navigate menus, and detect hold-vs-human-vs-transfer. Per the
honesty contract there is no real adapter — the swarm never dials a real payer.
The port carries no PHI: only the dialed specialist line, DTMF digits, and menu
state cross it.

This module defines the Protocol plus its request/result DTOs only; the sim
adapter lives in ``backstop.adapters.ivr``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from backstop.domain.enums import SpecialistKind

__all__ = [
    "IvrStage",
    "IvrSession",
    "IvrState",
    "IvrPort",
]


class IvrStage(str, Enum):
    """The coarse stage of an IVR interaction after a navigation step."""

    MENU = "menu"
    HOLD = "hold"
    HUMAN = "human"
    TRANSFER = "transfer"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class IvrSession:
    """A handle to an open sandbox IVR call.

    Attributes:
        session_id: Unique identifier for this IVR session.
        line: The specialist line that was dialed.
        number: The (sandbox) number dialed.
    """

    session_id: str
    line: SpecialistKind
    number: str


@dataclass(frozen=True)
class IvrState:
    """The state of an IVR session after dialing or navigating.

    Attributes:
        stage: The coarse interaction stage.
        prompt: The current menu/hold prompt text, when present.
        menu_path: The dot-joined DTMF path taken so far.
        hold_seconds: Estimated remaining hold time when on hold.
        reached_human: Whether a live human agent has been reached.
    """

    stage: IvrStage
    prompt: Optional[str] = None
    menu_path: str = ""
    hold_seconds: Optional[int] = None
    reached_human: bool = False


@runtime_checkable
class IvrPort(Protocol):
    """Async deterministic DTMF navigation over a sandbox IVR."""

    async def dial(self, number: str, line: SpecialistKind) -> IvrSession:
        """Dial the sandbox ``number`` for ``line`` and open a session."""
        ...

    async def navigate(self, session: IvrSession, dtmf: str) -> IvrState:
        """Push ``dtmf`` tones on ``session`` and return the resulting state."""
        ...

    async def hangup(self, session: IvrSession) -> None:
        """Hang up ``session``; idempotent."""
        ...
