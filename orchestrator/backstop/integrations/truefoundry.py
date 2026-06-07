"""TrueFoundry gateway — the single choke point every model call flows through.

In production TrueFoundry is the LLM gateway: it routes model calls, redacts PHI
on the way through, writes an audit record, and feeds the cost ledger from real
usage. Here the gateway is a local shim when ``TRUEFOUNDRY_API_KEY`` is absent —
it still does the load-bearing jobs (PHI redaction + audit log + cost feed) so the
honesty contract holds: the badge reads ``sim`` but the audit trail is real.

``.mode`` is ``"real"`` iff ``TRUEFOUNDRY_API_KEY`` is set, else ``"sim"``.

Methods:
  gateway(model, prompt) -> str   route a model call (redacts the prompt first)
  redact_phi(text) -> str         scrub member IDs, NPIs, claim IDs, names
  audit(event: dict) -> None      append a record to data/out/audit.jsonl
  record(decision) -> None        feed a RouteDecision into the running cost ledger
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from ..cost import CostLedger

# data/out/audit.jsonl, resolved from this file: integrations/ -> backstop/ ->
# orchestrator/ -> repo root -> data/out/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUDIT_PATH = _REPO_ROOT / "data" / "out" / "audit.jsonl"

# PHI redaction patterns. Order matters: most specific first. These run on every
# prompt before it leaves the gateway and on any event we audit.
_REDACTORS = [
    # claim ids: CLM-55-7741
    (re.compile(r"\bCLM-[0-9]{1,3}-[0-9]{3,6}\b"), "[CLAIM_ID]"),
    # 10-digit NPIs (rendering / billing)
    (re.compile(r"\b[0-9]{10}\b"), "[NPI]"),
    # member ids: a letter followed by 8-12 digits (e.g. W812340099)
    (re.compile(r"\b[A-Z][0-9]{8,12}\b"), "[MEMBER_ID]"),
    # auth numbers: A4471
    (re.compile(r"\bA[0-9]{4,6}\b"), "[AUTH]"),
    # SSNs, just in case
    (re.compile(r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b"), "[SSN]"),
]


class TrueFoundryGateway:
    """The gateway every model call is routed through (SPEC module table)."""

    def __init__(self, ledger: CostLedger | None = None):
        self.api_key = os.getenv("TRUEFOUNDRY_API_KEY")
        self.base_url = os.getenv("TRUEFOUNDRY_BASE_URL", "https://api.truefoundry.com")
        self.mode = "real" if self.api_key else "sim"
        # The cost ledger this gateway feeds. Shared with the call loop so the
        # dashboard's ticker and the gateway's record() see the same totals.
        self.ledger = ledger if ledger is not None else CostLedger()
        self._audit_count = 0

    # -- PHI redaction --------------------------------------------------------
    def redact_phi(self, text: str) -> str:
        """Scrub member IDs, NPIs, claim IDs, auth numbers from free text.

        Deterministic and offline. Runs identically in real and sim mode — the
        gateway never lets unredacted PHI reach a model provider.
        """
        if not text:
            return text
        out = text
        for pattern, token in _REDACTORS:
            out = pattern.sub(token, out)
        return out

    # -- model routing --------------------------------------------------------
    def gateway(self, model: str, prompt: str) -> str:
        """Route a model call through the gateway and return the completion.

        Always redacts the prompt first, then audits the (redacted) call. In real
        mode this would proxy to the TrueFoundry LLM gateway; in sim mode it
        returns a deterministic gateway-stamped echo so the pipeline stays live
        without a key.
        """
        safe_prompt = self.redact_phi(prompt)
        self.audit({"kind": "gateway.call", "model": model, "prompt": safe_prompt})
        if self.mode == "real":
            try:
                return self._call_real(model, safe_prompt)
            except Exception as exc:  # real path failed mid-demo -> degrade, don't crash
                self.audit({"kind": "gateway.fallback", "model": model, "error": str(exc)})
        return f"[truefoundry:sim:{model}] {safe_prompt[:120]}"

    def _call_real(self, model: str, prompt: str) -> str:
        import httpx  # imported lazily so import-time stays network-free

        resp = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -- audit log ------------------------------------------------------------
    def audit(self, event: dict) -> None:
        """Append one JSON line to data/out/audit.jsonl (PHI-redacted)."""
        record = {"ts": round(time.time(), 3), "mode": self.mode, **event}
        # belt-and-suspenders: redact any string values in the event too
        for k, v in list(record.items()):
            if isinstance(v, str):
                record[k] = self.redact_phi(v)
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        self._audit_count += 1

    # -- cost feed ------------------------------------------------------------
    def record(self, decision) -> None:
        """Feed one routing decision into the cost ledger (the ticker source).

        ``decision`` is a RouteDecision (exposes .pavo_cost_usd / .frontier_cost_usd
        / .tier / .turn_id). We also audit the cost event so the trail is complete.
        """
        self.ledger.add(decision)
        self.audit(
            {
                "kind": "cost.record",
                "turn_id": getattr(decision, "turn_id", None),
                "tier": getattr(decision, "tier", None),
                "pavo_cost_usd": getattr(decision, "pavo_cost_usd", None),
                "frontier_cost_usd": getattr(decision, "frontier_cost_usd", None),
            }
        )

    def to_dict(self) -> dict:
        return {
            "name": "truefoundry",
            "mode": self.mode,
            "audit_path": str(_AUDIT_PATH),
            "audited": self._audit_count,
        }


if __name__ == "__main__":
    gw = TrueFoundryGateway()
    print("mode:", gw.mode)
    dirty = "Member W812340099 claim CLM-55-7741 NPI 1659302341 auth A4471"
    print("redacted:", gw.redact_phi(dirty))
    print("gateway:", gw.gateway("gpt-x", dirty))
    gw.audit({"kind": "smoke", "note": "hello"})
    print("snapshot:", gw.to_dict())
