"""Backstop ⇄ Moss live retrieval microservice.

Moss's SDK needs Python >=3.10; the demo server runs 3.9 — so this tiny stdlib
HTTP service bridges them. On startup it builds (once) and loads a real Moss
index of the appeal rebuttals, then serves genuine semantic queries:

    GET /retrieve?q=<denial reason>   -> {id, text, score, ms, mode:"real"}
    GET /health                       -> {ok, index, docs, mode}

Run it on a Python 3.10+ venv that has `moss` installed (see scripts/run_moss.sh):
    MOSS_PROJECT_ID=... MOSS_PROJECT_KEY=... python scripts/moss_service.py

The Backstop server proxies /moss/retrieve here; if this service is down, the
dashboard simply falls back to the local TF-IDF retriever (honest degrade).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from moss import MossClient, QueryOptions  # type: ignore

try:  # DocumentInfo is optional; the SDK also accepts dicts
    from moss import DocumentInfo  # type: ignore
except Exception:  # pragma: no cover
    DocumentInfo = dict  # type: ignore

INDEX = os.getenv("MOSS_INDEX", "backstop-appeals-rebuttals")
PORT = int(os.getenv("MOSS_SERVICE_PORT", "8021"))
# loopback only: this is a local bridge for the orchestrator, never a public
# service. Override via MOSS_SERVICE_HOST only in a trusted network.
HOST = os.getenv("MOSS_SERVICE_HOST", "127.0.0.1")
# the dashboard never calls this directly (the orchestrator proxies it server-
# side), so the only legitimate browser origin is the local dashboard.
ALLOW_ORIGIN = os.getenv("MOSS_ALLOW_ORIGIN", "http://localhost:8000")

# The winning-rebuttal corpus (one doc per denial pattern). In production this
# is the compounding win-corpus; here it's the seed runbook set.
REBUTTALS = [
    ("co197", "CO-197 prior authorization absent. Run the auth-on-file lookup by the rendering NPI, not the billing NPI; the precertification on file satisfies the plan authorization requirement."),
    ("co50", "CO-50 not medically necessary. The chart documents failed conservative therapy meeting the plan LCD medical-necessity criterion."),
    ("co16", "CO-16 missing or incomplete information. The required field was present on the original 837 and dropped at the payer intake."),
    ("co45", "CO-45 charge exceeds the fee schedule. Reprocess at the contracted allowed amount per the fee schedule."),
    ("co197a", "Aetna CO-197 authorization A4471 was issued before the date of service under the rendering provider; reprocess and pay."),
    ("n130", "N130 plan provision. The service is covered under the member plan benefits; cite the plan document coverage section."),
]


def _mkdoc(doc_id: str, text: str):
    try:
        return DocumentInfo(id=doc_id, text=text)
    except Exception:
        return {"id": doc_id, "text": text}


_client: MossClient | None = None


async def _ensure_index() -> None:
    """Build the index if it doesn't exist, then load it for local query."""
    global _client
    pid = os.environ["MOSS_PROJECT_ID"]
    key = os.environ["MOSS_PROJECT_KEY"]
    _client = MossClient(pid, key)
    try:
        await _client.load_index(INDEX)
        print(f"[moss] loaded existing index '{INDEX}'", flush=True)
    except Exception:
        print(f"[moss] building index '{INDEX}' ({len(REBUTTALS)} rebuttals)…", flush=True)
        await _client.create_index(INDEX, [_mkdoc(i, t) for i, t in REBUTTALS])
        await _client.load_index(INDEX)
        print(f"[moss] index built + loaded", flush=True)


async def _query(q: str) -> dict:
    assert _client is not None
    t0 = time.time()
    res = await _client.query(INDEX, q, QueryOptions(top_k=1))
    ms = round((time.time() - t0) * 1000, 1)
    docs = getattr(res, "docs", None) or getattr(res, "results", None) or []
    if not docs:
        return {"mode": "real", "ms": ms, "id": "", "text": "", "score": 0.0}
    d = docs[0]
    return {
        "mode": "real",
        "ms": ms,
        "id": getattr(d, "id", ""),
        "text": getattr(d, "text", "") or getattr(d, "content", ""),
        "score": round(float(getattr(d, "score", 0.0)), 3),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/health":
            self._send(200, {"ok": _client is not None, "index": INDEX, "docs": len(REBUTTALS), "mode": "real"})
            return
        if u.path == "/retrieve":
            q = (parse_qs(u.query).get("q", [""])[0] or "").strip()
            if not q:
                self._send(400, {"error": "q required"})
                return
            try:
                self._send(200, _query_sync(q))
            except Exception as exc:  # never 500 the demo; log detail server-side only
                print(f"[moss] query error: {exc!r}", flush=True)
                self._send(200, {"mode": "error", "error": "retrieval error"})
            return
        self._send(404, {"error": "not found"})

    def log_message(self, *a):  # quiet
        return


def _query_sync(q: str) -> dict:
    return asyncio.run(_query(q))


def main() -> None:
    asyncio.run(_ensure_index())
    srv = HTTPServer((HOST, PORT), Handler)
    print(f"[moss] retrieval service live on http://{HOST}:{PORT}  (GET /retrieve?q=…)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
