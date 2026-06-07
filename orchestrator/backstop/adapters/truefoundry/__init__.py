"""TrueFoundry adapter package — the PHI boundary and LLM chokepoint.

This package implements four single-responsibility ports that together form the
TrueFoundry capability:

* :class:`~backstop.adapters.truefoundry.local_redaction_adapter.LocalRedactionAdapter`
  and its real wrapper — the sole producer of ``RedactedText``.
* :class:`~backstop.adapters.truefoundry.hashchain_audit_adapter.HashChainAuditAdapter`
  — a tamper-evident SHA-256 hash chain over model calls.
* :class:`~backstop.adapters.truefoundry.cost_ledger_adapter.CostLedgerAdapter`
  — a priced token/char usage ledger.
* :class:`~backstop.adapters.truefoundry.sim_gateway_adapter.SimGatewayAdapter`
  and ``tf_gateway_adapter.TrueFoundryGatewayAdapter`` — the single redacted LLM
  chokepoint, both injected the SAME redaction/audit/cost singletons.

The ``GatewayError`` raised by the gateway adapters lives here (the shared domain
error module is not edited by this workstream); it descends from
:class:`~backstop.domain.errors.BackstopError` so callers can still catch the base.
"""

from __future__ import annotations

from backstop.domain.errors import BackstopError

__all__ = ["GatewayError"]


class GatewayError(BackstopError):
    """Raised when the upstream LLM gateway fails after bounded retry.

    Carries only PHI-free context (the upstream HTTP status and a short reason);
    never the request/response bodies, which may contain model text.
    """

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        """Record the failure ``message`` and optional upstream ``status_code``."""
        super().__init__(message)
        self.status_code = status_code
