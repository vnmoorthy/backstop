"""Backstop server — FastAPI + WebSocket.

POST /appeals kicks off the async pipeline (Unsiloed intake -> swarm[PAVO+Moss+
cost] -> reconcile -> letter), broadcasting every event over WS /stream to the
dashboard. The masked PAVO router and the sponsor clients are instantiated once at
startup. Serves the dashboard (web/) and the generated appeal PDFs (data/out/).

Run:  cd orchestrator && python -m backstop.server   (or: uvicorn backstop.server:app)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .integrations import make_clients, sponsor_modes
from .ivr_sim import sample_denial
from .letter import draft_appeal
from .models import Denial
from .pavo import MaskedPAVORouter
from .reconcile import find_contradiction
from .swarm import Concierge, run_swarm

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB = _REPO_ROOT / "web"
_OUT = _REPO_ROOT / "data" / "out"
_DENIALS = _REPO_ROOT / "data" / "denials"


def _load_dotenv() -> None:
    """Load repo-root .env (if present) so sponsor keys flip badges to real.
    No dependency; does not override already-set environment variables."""
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k and v and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

# --- singletons (built once, after .env is loaded) ---------------------------
ROUTER = MaskedPAVORouter()
CLIENTS = make_clients()
APPEALS: dict[str, dict] = {}  # appeal_id -> {events:[...], status}


class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()
        self._send_lock = asyncio.Lock()  # serialize broadcasts; worker threads schedule many

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast(self, message: dict):
        # many swarm worker threads schedule broadcasts via run_coroutine_threadsafe;
        # without this lock two coroutines could interleave at `await ws.send_json`
        # on the same socket and corrupt frames.
        async with self._send_lock:
            for ws in list(self.active):
                try:
                    await ws.send_json(message)
                except Exception:
                    self.disconnect(ws)


def _cors_origins() -> list[str]:
    """Explicit CORS allowlist (never '*'). Override via BACKSTOP_CORS_ALLOW_ORIGINS
    (comma-separated). Defaults to the local dashboard + the published Pages site.
    A wildcard would let any website drive the API and read appeal data
    cross-origin from a victim's browser."""
    raw = os.getenv("BACKSTOP_CORS_ALLOW_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://vnmoorthy.github.io",
    ]


manager = ConnectionManager()
app = FastAPI(title="Backstop", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request, call_next):
    """Fresh dashboard + baseline hardening headers on every response."""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _make_emit(appeal_id: str, loop: asyncio.AbstractEventLoop):
    seq = {"n": 0}
    lock = threading.Lock()  # emit is called concurrently from swarm worker threads

    def emit(etype: str, payload: dict) -> None:
        # build the event + assign the seq + append under the lock so the seq is
        # never lost to a read-modify-write race and the broadcast order matches
        # the append order.
        with lock:
            seq["n"] += 1
            event = {
                "type": etype,
                "appeal_id": appeal_id,
                "ts": round(time.time(), 3),
                "seq": seq["n"],
                **payload,
            }
            APPEALS[appeal_id]["events"].append(event)
            asyncio.run_coroutine_threadsafe(manager.broadcast(event), loop)

    return emit


def _moss_live(query: str) -> Optional[dict]:
    """Query the live Moss retrieval service (the REAL Moss API, via the
    scripts/moss_service.py bridge). Returns the result dict, or None if the
    service isn't running — the demo then degrades to the local retriever."""
    import httpx

    url = os.getenv("MOSS_SERVICE_URL", "http://localhost:8021") + "/retrieve"
    try:
        r = httpx.get(url, params={"q": query}, timeout=4.0)
        if r.status_code == 200:
            data = r.json()
            return data if data.get("mode") == "real" else None
    except Exception:
        pass
    return None


async def _run_pipeline(appeal_id: str, denial: Denial) -> None:
    loop = asyncio.get_running_loop()
    emit = _make_emit(appeal_id, loop)
    gw = CLIENTS["truefoundry"]
    try:
        for name, mode in sponsor_modes(CLIENTS).items():
            emit("sponsor.mode", {"name": name, "mode": mode})
        spec = Concierge.intake(denial, parse_confidence=0.86)
        emit("intake.parsed", _redact_spec_for_wire(spec.to_dict(), gw))
        out = await run_swarm(spec, ROUTER, CLIENTS["moss"], emit, gateway=gw)
        contra = find_contradiction(out["transcripts_by_agent"])
        if contra:
            # redact the verbatim quotes before they hit the wire (defense-in-depth)
            cd = contra.to_dict()
            cd["claim"] = gw.redact_phi(cd.get("claim", ""))
            cd["evidence"] = gw.redact_phi(cd.get("evidence", ""))
            emit("reconcile.found", cd)

            # LIVE Moss: the Rebuttal-Retrieval agent queries the REAL Moss index
            # with the payer's denial assertion and surfaces the winning rebuttal
            # via genuine semantic search (sub-12ms). Off-thread; degrades cleanly
            # to the local retriever if the Moss service isn't running.
            moss_hit = await asyncio.to_thread(_moss_live, contra.claim)
            if moss_hit:
                emit("moss.live", moss_hit)

            # REAL reasoning: compose the appeal argument with MiniMax, routed
            # through the TrueFoundry gateway (PHI redacted on the way out +
            # audited). This is a genuine LLM call when MINIMAX_API_KEY is set;
            # it falls back to a grounded local line otherwise. Run off-thread so
            # the blocking HTTP call never stalls the event loop.
            mm = CLIENTS["minimax"]
            reb = out.get("rebuttal")
            prompt = gw.redact_phi(
                f"You are a hospital appeals specialist on a call with {denial.payer}. "
                f"Claim {denial.claim_id} was denied {denial.denial_code}. "
                f'The payer rep asserted: "{contra.claim}". '
                f'But the record shows: "{contra.evidence}". '
                + (f'Winning precedent: "{reb.magic_words}". ' if reb else "")
                + "In 2 crisp sentences, state the exact appeal argument that overturns this denial."
            )
            try:
                argument = await asyncio.to_thread(mm.complete, prompt)
            except Exception as _exc:  # never break the demo on a model hiccup
                argument = ""
            gw.audit({"kind": "reason.compose", "model": getattr(mm, "model", ""),
                      "mode": getattr(mm, "mode", "sim"), "chars": len(argument or "")})
            emit("agent.compose", {
                "text": gw.redact_phi(argument or ""),
                "mode": getattr(mm, "mode", "sim"),
                "model": getattr(mm, "model", ""),
            })

            letter = draft_appeal(
                spec, contra, out["transcripts_by_agent"], out["rebuttal"], appeal_id=appeal_id
            )
            pdf_name = Path(letter.pdf_path).name if letter.pdf_path else ""
            d = letter.to_dict()
            d["pdf_url"] = f"/files/{pdf_name}" if pdf_name else ""
            emit("letter.ready", d)
        APPEALS[appeal_id]["status"] = "done"
        emit("appeal.done", {"cost": out["cost"]})
    except Exception as exc:  # never leave the demo hanging silently
        APPEALS[appeal_id]["status"] = "error"
        # log the detail server-side; never leak exception internals to clients
        print(f"[backstop] pipeline error for {appeal_id}: {exc!r}", flush=True)
        emit("appeal.error", {"error": "internal error"})


def _mask_tail(value: str, keep: int = 4) -> str:
    s = str(value or "")
    if not s:
        return s
    # never reveal more than half (and never the whole) of a short identifier —
    # the old `s[-keep:]` returned the full string when len(s) <= keep.
    show = min(keep, len(s) // 2)
    return "*" * (len(s) - show) + s[len(s) - show:]


def _redact_spec_for_wire(spec: dict, gateway) -> dict:
    """Defense-in-depth: scrub PHI from the intake event before it hits the wire.

    The dashboard never needs the member ID or full NPIs; we mask them (and run
    raw_text through the TrueFoundry gateway's redactor) so PHI does not leave the
    server even though the demo data is synthetic.
    """
    d = dict(spec.get("denial", {}))
    d["member_id"] = _mask_tail(d.get("member_id", ""))
    d["rendering_npi"] = _mask_tail(d.get("rendering_npi", ""))
    d["billing_npi"] = _mask_tail(d.get("billing_npi", ""))
    if d.get("raw_text"):
        d["raw_text"] = gateway.redact_phi(d["raw_text"])
    spec = dict(spec)
    spec["denial"] = d
    return spec


def _denial_from_dict(d: dict) -> Denial:
    fields = {
        "denial_id": d.get("denial_id", "DEN-" + uuid.uuid4().hex[:6]),
        "payer": d.get("payer", "Aetna"),
        "plan": d.get("plan", "Unknown plan"),
        "state": d.get("state", "TX"),
        "denial_code": d.get("denial_code", "CO-197"),
        "cpt": d.get("cpt", ["99285"]),
        "billed_amount": float(d.get("billed_amount", 0) or 0),
        "date_of_service": d.get("date_of_service", "2026-03-14"),
        "member_id": d.get("member_id", "W812340099"),
        "claim_id": d.get("claim_id", "CLM-55-7741"),
        "rendering_npi": d.get("rendering_npi", "1659302341"),
        "billing_npi": d.get("billing_npi", "1093847551"),
        "raw_text": d.get("raw_text", ""),
    }
    return Denial(**fields)


# --- API ---------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "pavo_params": ROUTER.n_params, "sponsors": sponsor_modes(CLIENTS)}


@app.get("/samples")
async def samples():
    out = []
    if _DENIALS.exists():
        for p in sorted(_DENIALS.glob("*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except Exception:
                continue
    if not out:
        out.append(sample_denial().to_dict())
    return out


def _load_sample(denial_id: str) -> Denial:
    """Load a seeded synthetic denial by its denial_id; fall back to the default."""
    if _DENIALS.exists():
        for p in sorted(_DENIALS.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                if d.get("denial_id") == denial_id:
                    return _denial_from_dict(d)
            except Exception:
                continue
    return sample_denial()


@app.post("/appeals")
async def create_appeal(
    denial_id: Optional[str] = None,
    denial: Optional[UploadFile] = File(default=None),
):
    """Start an appeal. Pick a seeded sample by denial_id, upload an EOB, or default."""
    if denial is not None:
        raw = (await denial.read()).decode("utf-8", errors="ignore")
        # allow_path stays False: an uploaded body must NEVER be read as a server
        # filesystem path (that would disclose /app/.env, /etc/passwd, …).
        parsed = CLIENTS["unsiloed"].parse_eob(raw, allow_path=False)
        denial_obj = _denial_from_dict(parsed)
    elif denial_id:
        denial_obj = _load_sample(denial_id)
    else:
        denial_obj = sample_denial()

    # unguessable id (~72 bits) — the old 32-bit uuid4()[:8] was brute-forceable,
    # and the appeal record / PDF filename are keyed off it.
    appeal_id = "AP-" + secrets.token_urlsafe(12)
    # store a redacted projection of the denial: GET /appeals/{id} returns this
    # record, so the raw member id / NPIs / free text must never sit in it.
    masked_denial = _redact_spec_for_wire(
        {"denial": denial_obj.to_dict()}, CLIENTS["truefoundry"]
    )["denial"]
    APPEALS[appeal_id] = {"events": [], "status": "running", "denial": masked_denial}
    asyncio.create_task(_run_pipeline(appeal_id, denial_obj))
    return {"appeal_id": appeal_id}


@app.get("/appeals/{appeal_id}")
async def get_appeal(appeal_id: str):
    a = APPEALS.get(appeal_id)
    if not a:
        return JSONResponse({"error": "not found"}, status_code=404)
    return a


@app.websocket("/stream")
async def stream(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive; clients may ping
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# --- generated appeal PDFs (guarded; NOT a raw directory mount) ---------------
_OUT.mkdir(parents=True, exist_ok=True)
_PDF_NAME = re.compile(r"^appeal_[A-Za-z0-9_\-]{6,48}\.pdf$")


@app.get("/files/{name}")
async def get_file(name: str):
    """Serve only generated appeal PDFs. A raw StaticFiles mount over data/out
    would also expose audit.jsonl and any other artifact, and the names would be
    guessable — so we allow exactly ``appeal_<id>.pdf`` and re-check the resolved
    path stays inside _OUT (defense against traversal)."""
    if not _PDF_NAME.match(name):
        return JSONResponse({"error": "not found"}, status_code=404)
    target = (_OUT / name).resolve()
    if target.parent != _OUT.resolve() or not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(target), media_type="application/pdf")


# --- static dashboard (registered last so API routes win) ---------------------
if _WEB.exists():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="web")


def main():
    import uvicorn

    # default to loopback: an unauthenticated PHI-handling server must not bind
    # all interfaces (LAN exposure at a venue). Set BACKSTOP_HOST=0.0.0.0 only
    # in a trusted/containerized deployment.
    host = os.getenv("BACKSTOP_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
