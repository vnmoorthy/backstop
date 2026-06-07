"""Moss — the real-time retrieval brain of every call.

In production Moss is the sub-10ms semantic-search runtime; here, when MOSS_API_KEY
is absent, we stand in a deterministic local retriever over data/runbooks/*.md so
the pipeline runs offline. Either way Moss is load-bearing: on the turn the rep
states the denial reason, the calling agent retrieves the winning rebuttal + the
precedent that overturns the denial, mid-call.

.mode == "real" iff MOSS_API_KEY is set.

Methods (SPEC module table):
  retrieve_rebuttal(denial_code, payer) -> Rebuttal | None
  retrieve_ivr_path(payer) -> str
  retrieve_precedent(query) -> str
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

from ..models import Rebuttal

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOKS = _REPO_ROOT / "data" / "runbooks"

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body). Frontmatter is a leading ---\\n...\\n--- block."""
    fm: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            body = text[end + 4 :]
            for line in block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
    return fm, body


def _extract_magic_words(body: str) -> str:
    """Pull the bold blockquote under the 'winning rebuttal' heading."""
    # find the section, then the first blockquote line(s)
    m = re.search(r"winning rebuttal.*?\n(.*?)(?:\n#|\Z)", body, re.IGNORECASE | re.DOTALL)
    chunk = m.group(1) if m else body
    quote = re.findall(r"^>\s*(.+)$", chunk, re.MULTILINE)
    if quote:
        joined = " ".join(q.strip() for q in quote)
        joined = joined.replace("**", "").strip().strip('"')
        return joined
    # fallback: first bold span anywhere
    b = re.search(r"\*\*(.+?)\*\*", body, re.DOTALL)
    return (b.group(1).strip() if b else "Request a re-adjudication citing the auth on file.")


def _extract_ivr_path(body: str) -> str:
    m = re.search(r"IVR path.*?\n+`([^`]+)`", body, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else "Main menu -> press 3 (claims) -> hold for a rep"


class _Runbook:
    def __init__(self, path: Path):
        text = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        self.source_id = fm.get("runbook_id", path.stem.upper())
        self.payer = fm.get("payer", "")
        self.denial_code = fm.get("denial_code", "")
        try:
            self.win_rate = float(fm.get("win_rate", "0.5"))
        except ValueError:
            self.win_rate = 0.5
        self.magic_words = _extract_magic_words(body)
        self.ivr_path = _extract_ivr_path(body)
        self.body = body
        self.tokens = _tokens(body)


class MossClient:
    def __init__(self):
        self.api_key = os.getenv("MOSS_API_KEY")
        self.base_url = os.getenv("MOSS_BASE_URL", "https://api.usemoss.dev")
        self.mode = "real" if self.api_key else "sim"
        self._runbooks: list[_Runbook] = []
        self._df: dict[str, int] = {}
        self._load_corpus()

    def _load_corpus(self) -> None:
        if not _RUNBOOKS.exists():
            return
        for p in sorted(_RUNBOOKS.glob("*.md")):
            try:
                rb = _Runbook(p)
                self._runbooks.append(rb)
                for w in set(rb.tokens):
                    self._df[w] = self._df.get(w, 0) + 1
            except Exception:
                continue

    # -- exact (payer, denial_code) lookup -> the winning rebuttal -----------
    def retrieve_rebuttal(self, denial_code: str, payer: str) -> Rebuttal | None:
        dc = (denial_code or "").upper()
        py = (payer or "").lower()
        # exact payer+code, then code-only, then any
        for want_payer in (True, False):
            for rb in self._runbooks:
                if rb.denial_code.upper() == dc and (not want_payer or rb.payer.lower() == py):
                    return Rebuttal(
                        denial_code=rb.denial_code or dc,
                        payer=rb.payer or payer,
                        magic_words=rb.magic_words,
                        win_rate=rb.win_rate,
                        source_id=rb.source_id,
                    )
        return None

    def retrieve_ivr_path(self, payer: str) -> str:
        py = (payer or "").lower()
        for rb in self._runbooks:
            if rb.payer.lower() == py:
                return rb.ivr_path
        return "Main menu -> press 3 (claims) -> hold for a rep"

    # -- TF-IDF precedent search across runbook bodies ----------------------
    def retrieve_precedent(self, query: str) -> str:
        if not self._runbooks:
            return ""
        n = len(self._runbooks)
        q = _tokens(query)
        best, best_score = None, -1.0
        for rb in self._runbooks:
            tf: dict[str, int] = {}
            for w in rb.tokens:
                tf[w] = tf.get(w, 0) + 1
            score = 0.0
            for w in q:
                if w in tf:
                    idf = math.log((n + 1) / (1 + self._df.get(w, 0))) + 1.0
                    score += (tf[w] / len(rb.tokens)) * idf
            if score > best_score:
                best, best_score = rb, score
        if best is None:
            return ""
        # return the most relevant paragraph
        paras = [p.strip() for p in best.body.split("\n\n") if len(p.strip()) > 40]
        for p in paras:
            if any(w in p.lower() for w in q):
                return p.replace("\n", " ")[:400]
        return (paras[0].replace("\n", " ")[:400]) if paras else ""

    def to_dict(self) -> dict:
        return {"name": "moss", "mode": self.mode, "runbooks": len(self._runbooks)}


if __name__ == "__main__":
    m = MossClient()
    print("mode:", m.mode, "| runbooks:", len(m._runbooks))
    r = m.retrieve_rebuttal("CO-197", "Aetna")
    print("rebuttal:", r.to_dict() if r else None)
    print("ivr:", m.retrieve_ivr_path("Aetna"))
    print("precedent:", m.retrieve_precedent("no auth on file rendering npi")[:160])
