"""Deterministic sandbox IVR adapter for :class:`IvrPort` (sim-only).

Implements :class:`backstop.ports.ivr_port.IvrPort` as a genuine deterministic
DTMF state machine -- not a string echo. Per the honesty contract (SPEC §2 N1)
the swarm never dials a real payer, so this is the *only* IVR adapter. It
reproduces the shape of a real provider-line call: a multi-level menu, a hold
state with a decreasing estimated wait, then a live human after the correct DTMF
path. The same digit path always yields the same outcome, so call-flow tests are
reproducible.

No PHI crosses this port: only the dialed specialist line, DTMF digits, and menu
state are handled. This module imports only the standard library plus
:mod:`backstop.domain`/:mod:`backstop.ports`; it imports no vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from backstop.domain.enums import SpecialistKind
from backstop.ports.id_gen_port import IdGenPort
from backstop.ports.ivr_port import IvrSession, IvrStage, IvrState

__all__ = ["IvrSimAdapter"]

# The DTMF digit that, from the claims menu, routes to a live agent (after hold).
_CLAIMS_DIGIT = "3"
# The DTMF digit that hangs up / exits the tree.
_EXIT_DIGIT = "0"
# Estimated initial hold once the claims queue is entered (seconds).
_INITIAL_HOLD_SECONDS = 840  # 14 minutes, matching the sandbox scenarios.
# Each "still holding" navigation step decreases the estimate by this much.
_HOLD_DECREMENT_SECONDS = 120


@dataclass
class _SessionState:
    """Mutable per-session machine state (held internally, never on the port)."""

    line: SpecialistKind
    number: str
    menu_path: str = ""
    hold_seconds: Optional[int] = None
    in_claims_queue: bool = False
    reached_human: bool = False
    disconnected: bool = False
    extra: Dict[str, str] = field(default_factory=dict)


class IvrSimAdapter:
    """Deterministic sandbox :class:`~backstop.ports.ivr_port.IvrPort`.

    Construction injects the id generator (for session ids). State for each open
    session is held in-process and removed on :meth:`hangup`.
    """

    def __init__(self, *, id_gen: IdGenPort) -> None:
        """Start with no open sessions."""
        self._id_gen = id_gen
        self._sessions: Dict[str, _SessionState] = {}

    async def dial(self, number: str, line: SpecialistKind) -> IvrSession:
        """Dial the sandbox ``number`` for ``line`` and open a session.

        The session id is server-minted; the opening state is the top-level menu
        prompt for ``line``.
        """
        session_id = self._id_gen.new_id()
        self._sessions[session_id] = _SessionState(line=line, number=number)
        return IvrSession(session_id=session_id, line=line, number=number)

    async def navigate(self, session: IvrSession, dtmf: str) -> IvrState:
        """Push ``dtmf`` tones on ``session`` and return the resulting state.

        The transition is a pure function of the current machine state and the
        digits, so an identical path always reproduces the same outcome:

        * ``0`` from any menu disconnects.
        * ``3`` from the top menu enters the claims hold queue.
        * Any digit while holding advances the queue (estimate decreases) and,
          once the estimate reaches zero, a live human answers.
        """
        state = self._sessions.get(session.session_id)
        if state is None or state.disconnected:
            return IvrState(stage=IvrStage.DISCONNECTED)

        digits = _normalise_dtmf(dtmf)
        for digit in digits:
            self._step(state, digit)
            if state.disconnected or state.reached_human:
                break

        return _render(state)

    async def hangup(self, session: IvrSession) -> None:
        """Hang up ``session``; idempotent."""
        state = self._sessions.get(session.session_id)
        if state is not None:
            state.disconnected = True
            self._sessions.pop(session.session_id, None)

    # ------------------------------------------------------------------ #
    # State machine.
    # ------------------------------------------------------------------ #
    def _step(self, state: _SessionState, digit: str) -> None:
        """Advance ``state`` by a single DTMF ``digit``."""
        state.menu_path = f"{state.menu_path}.{digit}" if state.menu_path else digit

        if digit == _EXIT_DIGIT:
            state.disconnected = True
            return

        if state.in_claims_queue:
            # Already holding: each tone advances the queue toward a human.
            assert state.hold_seconds is not None  # noqa: S101 - invariant
            state.hold_seconds = max(0, state.hold_seconds - _HOLD_DECREMENT_SECONDS)
            if state.hold_seconds == 0:
                state.reached_human = True
                state.in_claims_queue = False
            return

        if digit == _CLAIMS_DIGIT:
            # Enter the claims hold queue.
            state.in_claims_queue = True
            state.hold_seconds = _INITIAL_HOLD_SECONDS
            return

        # Any other top-level digit loops back to the menu (deterministic).


def _render(state: _SessionState) -> IvrState:
    """Project the internal machine state onto the port's :class:`IvrState`."""
    if state.disconnected:
        return IvrState(stage=IvrStage.DISCONNECTED, menu_path=state.menu_path)
    if state.reached_human:
        return IvrState(
            stage=IvrStage.HUMAN,
            prompt="Provider services, how can I help you?",
            menu_path=state.menu_path,
            reached_human=True,
        )
    if state.in_claims_queue:
        hold_seconds = state.hold_seconds if state.hold_seconds is not None else 0
        return IvrState(
            stage=IvrStage.HOLD,
            prompt=f"Please continue to hold. Estimated wait {hold_seconds // 60} minutes.",
            menu_path=state.menu_path,
            hold_seconds=hold_seconds,
        )
    return IvrState(
        stage=IvrStage.MENU,
        prompt=_menu_prompt(state.line),
        menu_path=state.menu_path,
    )


def _menu_prompt(line: SpecialistKind) -> str:
    """Return the deterministic top-level menu prompt for a specialist line."""
    desk = {
        SpecialistKind.PROVIDER_LINE: "provider services",
        SpecialistKind.BILLING_OFFICE: "the billing office",
        SpecialistKind.RECORDS_DESK: "medical records",
        SpecialistKind.PRIOR_AUTH_DESK: "prior authorization",
    }[line]
    return f"Thank you for calling {desk}. For claims, press {_CLAIMS_DIGIT}."


def _normalise_dtmf(dtmf: str) -> str:
    """Keep only valid DTMF symbols from ``dtmf`` (digits, ``*`` and ``#``).

    A hash of the raw input is intentionally *not* used for routing; only the
    literal digits drive the machine, keeping it inspectable and deterministic.
    """
    allowed = set("0123456789*#")
    return "".join(ch for ch in dtmf if ch in allowed)
