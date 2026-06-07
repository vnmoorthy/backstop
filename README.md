<div align="center">

# BACKSTOP

### Win the phone appeal.

**A voice-agent swarm that recovers denied health-insurance claims by doing the one thing nobody automates — the phone appeal.** A denial detonates into a swarm of AI agents that call the payer's provider line, prior-auth desk, and records desk *in parallel*, sit the holds, retrieve the exact rebuttal in real time, find the contradiction that overturns the denial, and hand a human appeals nurse a verbatim, audit-grade appeal letter to sign and file.

**Powered by [PAVO](https://huggingface.co/datasets/vnmoorthy/pavo-bench) — pipeline-aware demand-conditioned routing (TMLR 2026).**

`PAVO $0.024  ·  frontier $0.105  ·  4.4x cheaper per appeal` *(verified, this repo)*

![Backstop dashboard](docs/img/dashboard.png)

</div>

---

## The problem

US providers write off **~$262B/yr** in denied claims. **~65% are never appealed** — not because they'd lose, but because no human will sit on hold 25 minutes to recover a $30 line item. The recovery work is *phone* work, and at frontier-model cost a per-appeal call loses money. That's the gap.

## Why it pencils: PAVO

A single appeal is 6-12 calls, each 30-60 turns, most of them IVR navigation and hold music. **PAVO routes ~90% of those turns to a near-free local model and spends a frontier model only on the 2-3 turns where the rep states the denial reason** — a measured **~5x cost collapse** that flips a $30 appeal from underwater to profitable.

PAVO's released policy is constant; the routing intelligence is the **coupling mask** (the paper's contribution): for a turn of complexity *C*, profiles that can't serve *C* are masked out, so the choice escalates as the turn gets harder. Verified in `orchestrator/tests/test_router.py`:

```
complexity  profile   tier        feasible   latency
    1          2     local_fast      48        0.58x   ← IVR nav / hold
    5         40     frontier         9        2.17x   ← the denial-reason turn
```

## The clients

Mid-market **hospital billing departments + outsourced RCM / medical-billing companies**, paid on **contingency (25-30% of recovered dollars)**. First design partner: a regional system with a 3,000-denial backlog already written off — every dollar recovered is found money.

## All 7 sponsors, load-bearing

| Sponsor | Role in Backstop |
|---|---|
| **PAVO** *(founder IP)* | Per-turn masked routing — the cost collapse. `pavo/router.py` |
| **Moss** | The real-time retrieval brain: the winning rebuttal + precedent fired the instant the rep states the denial reason. `integrations/moss.py` |
| **LiveKit** | Voice transport for the concurrent call swarm + the nurse bridge. |
| **TrueFoundry** | The gateway every model call flows through: PHI redaction + immutable audit log + the cost ledger the ticker reads. |
| **Unsiloed** | Parses the denial EOB / CMS-1500 into the structured appeal spec. |
| **AWS** | Elastic burst to hundreds of concurrent call containers, then scale to zero. |
| **MiniMax** | Mid-tier reasoning + multilingual completion. |
| **Qwen** | One consistent "appeals coordinator" brand voice, multilingual TTS. |

Every integration exposes a `.mode` flag — `real` when its API key is present, a deterministic local `sim` otherwise — and the dashboard shows a live badge per sponsor. **Nothing is misrepresented as real.** Drop keys in `.env` to flip badges to `real`.

## Run it

```bash
cd orchestrator
pip install -r requirements.txt
python -m backstop.server          # http://localhost:8000
# open http://localhost:8000/ in a browser → "Run sample denial"
```

Headless (no browser):

```bash
cd orchestrator && python -m backstop.swarm     # watch the swarm + cost collapse
python ../scripts/run_demo.py                    # full pipeline, headless
```

Tests:

```bash
cd orchestrator && python tests/test_router.py && python tests/test_call.py
```

## What's real vs simulated (the honesty contract)

| Real | Simulated (by policy / for the demo) |
|---|---|
| PAVO router + weights (TMLR) | Payer IVR / rep (sandbox — **we never dial real payers**) |
| Cost ledger + the 4.4x ratio (real token/tier math) | Denial data (synthetic — **no PHI**) |
| Moss retrieval over real runbooks | Sponsor APIs default to `sim` until keys are set |
| Reconciler, appeal-letter PDF, audit log | Nurse sign-off (UI toggle) |

## Compliance posture

Provider-to-payer **B2B lines only** (BAA'd agent, AI disclosed at call open, consent-clean). The swarm **never files** — a licensed appeals nurse signs. TrueFoundry redacts PHI before any model call and writes an immutable audit trail.

## Architecture

`docs/SPEC.md` (full engineering spec) · `docs/ARCHITECTURE.md` (diagram + contracts) · `docs/DEMO_SCRIPT.md` (90-second runbook).

```
denial → Unsiloed intake → Concierge → SWARM (parallel calls, each: PAVO route + Moss retrieve)
       → Reconciler (find the contradiction) → Letter (verbatim PDF) → Nurse signs
       → every step streamed over WebSocket to the dashboard
```

---

<div align="center">
<sub>Synthetic data only · sandbox IVRs · human nurse sign-off required · built for the YC Conversational AI Hackathon</sub>
</div>
