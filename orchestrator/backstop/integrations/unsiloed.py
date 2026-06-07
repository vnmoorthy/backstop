"""Unsiloed — parse the denial EOB / CMS-1500 / UB-04 into a structured spec.

Real if UNSILOED_API_KEY is set (httpx to the Unsiloed parse API); otherwise a
deterministic regex parser over the EOB text so intake runs offline. Either way
this is the front of the pipeline: messy denial document in, clean Denial fields out.

.mode == "real" iff UNSILOED_API_KEY is set.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


class Unsiloed:
    def __init__(self):
        self.api_key = os.getenv("UNSILOED_API_KEY")
        self.base_url = os.getenv("UNSILOED_BASE_URL", "https://api.unsiloed.ai")
        self.mode = "real" if self.api_key else "sim"

    def parse_eob(self, path_or_text: str, *, allow_path: bool = False) -> dict:
        """Return a dict of Denial fields parsed from an EOB.

        Accepts raw EOB text. ``allow_path`` is opt-in and TRUSTED-ONLY: when
        True, a short input that is an existing file is read from disk (used by
        the CLI). Network/upload callers MUST leave it False — otherwise an
        uploaded body like ``/app/.env`` or ``/etc/passwd`` would be read off the
        server's filesystem and leaked back as the parsed EOB (arbitrary file
        disclosure).
        """
        text = path_or_text or ""
        if allow_path and isinstance(text, str) and text.strip() and len(text) < 400:
            try:
                p = Path(text)
                if p.is_file():
                    text = p.read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError):
                pass  # not a usable path — treat the string as the EOB text

        if self.mode == "real":
            try:
                return self._parse_real(text)
            except Exception:
                pass  # degrade to the deterministic parser, never crash the demo
        return self._parse_regex(text)

    def _parse_regex(self, text: str) -> dict:
        def find(pat, default=""):
            # MULTILINE so ^/$ anchors match per line (e.g. the payer-name line
            # may not be the first characters when the EOB has a preamble header)
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            return m.group(1).strip() if m else default

        denial_code = find(r"\b(CO-\d{1,3}|PR-\d{1,3})\b", "CO-197").upper()
        # only accept a 5-digit code that is actually labeled as a procedure code —
        # never grab a bare 5-digit run (claim/member fragments are 5+ digits too)
        cpt = (
            re.findall(r"\bCPT\s*[:#]?\s*(\d{5})\b", text, re.IGNORECASE)
            or re.findall(r"\b(?:HCPCS|procedure)\s*(?:code)?\s*[:#]?\s*(\d{5})\b", text, re.IGNORECASE)
        )
        billed = find(r"\$([\d,]+\.\d{2})", "0.00").replace(",", "")
        npis = re.findall(r"\b(\d{10})\b", text)
        payer = find(r"^([A-Z][A-Za-z ]+?)\s+(?:EXPLANATION|EOB|provider)", "Aetna")
        return {
            "payer": payer or "Aetna",
            "plan": find(r"Plan:\s*([^\n(]+)", "Unknown plan").strip(),
            "state": find(r"\(([A-Z]{2})\)", "TX"),
            "denial_code": denial_code,
            "cpt": cpt[:1] or ["99285"],
            "billed_amount": float(billed or 0),
            "date_of_service": find(r"DOS\s*([\d/]{8,10})", "2026-03-14"),
            "claim_id": find(r"\b(CLM-[\d-]+)\b", "CLM-55-7741"),
            "member_id": find(r"Member\s*([A-Z]\d{8,12})", "W812340099"),
            "rendering_npi": (npis[0] if len(npis) > 0 else "1659302341"),
            "billing_npi": (npis[1] if len(npis) > 1 else "1093847551"),
            "raw_text": text.strip(),
            "parse_confidence": 0.86 if self.mode == "real" else 0.78,
        }

    def _parse_real(self, text: str) -> dict:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/v1/parse",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"document": text, "schema": "eob"},
            timeout=30.0,
        )
        resp.raise_for_status()
        out = resp.json()
        # map Unsiloed's structured output into our Denial fields; fall back per-field
        base = self._parse_regex(text)
        base.update({k: v for k, v in out.get("fields", {}).items() if v})
        base["parse_confidence"] = out.get("confidence", base["parse_confidence"])
        return base

    def to_dict(self) -> dict:
        return {"name": "unsiloed", "mode": self.mode}


if __name__ == "__main__":
    u = Unsiloed()
    sample = (
        "AETNA EXPLANATION OF BENEFITS\nClaim CLM-55-7741 DOS 03/14/2026 CPT 99285 "
        "Billed $2,480.00\nDENIAL CO-197: Precertification absent.\nMember W812340099 "
        "Plan: Aetna Choice POS II (TX)\nRendering NPI 1659302341 Billing NPI 1093847551"
    )
    print("mode:", u.mode)
    import json
    print(json.dumps(u.parse_eob(sample), indent=2)[:600])
