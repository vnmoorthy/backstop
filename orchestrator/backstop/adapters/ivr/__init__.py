"""IVR adapters: deterministic sandbox DTMF navigation (sim-only).

Houses :class:`~backstop.adapters.ivr.ivr_sim_adapter.IvrSimAdapter`, the only
implementation of :class:`backstop.ports.ivr_port.IvrPort`. Per the honesty
contract there is no real adapter -- the swarm never dials a real payer -- so the
sim does genuine local work: a deterministic menu state machine driven by DTMF
digits. No PHI crosses the port.
"""

from __future__ import annotations
