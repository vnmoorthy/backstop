# Backstop — Engineering Spec

> The voice-agent swarm that recovers denied health-insurance claims by winning the phone appeal.
> Built for the YC Conversational AI Hackathon. Powered by the PAVO router (TMLR 2026).

Status: build spec, v1. Owner: @vnmoorthy. Target: end-to-end runnable demo in ~8h.

---

## 1. Problem & thesis

US providers write off ~$262B/yr in initially denied claims. ~65% are never appealed — not because they would lose, but because a human will not sit on hold 25 minutes to recover a $30 line item. The recovery work is phone work: call the payer provider line, navigate the IVR, hold, extract the rep's verbatim denial reason, find the contradiction, file the appeal.

Backstop automates that phone work as a parallel swarm of voice agents. The economics only close because of **PAVO**: ~90% of call turns (IVR nav, "please hold") route to a near-free local model; a frontier model is spent only on the 2-3 turns that carry the denial reason. That is a ~5x cost collapse — the difference between a $30 appeal that makes money and one that loses it.

**Clients:** mid-market hospital billing departments + outsourced RCM/medical-billing companies. Contingency pricing (25-30% of recovered dollars). First design partner: a regional system with a 3,000-denial written-off backlog.

**Legal wedge:** provider-to-payer B2B lines only. We are the covered entity's BAA'd agent placing a business call. AI disclosed at call open. Consent-clean recording. The swarm never files — a human appeals nurse signs.

---

## 2. Goals / Non-goals

### Goals (demo + foundation)
- G1. Upload one denial document → parse to a structured appeal spec.
- G2. Detonate a swarm of specialist call-agents, dispatched by denial-code gates.
- G3. Each call runs through the **PAVO masked router**, per-turn, choosing a model tier; emit the routing decision + cost per turn.
- G4. **Moss** retrieval fires per turn: IVR path, winning rebuttal the instant the rep objects, precedent.
- G5. Reconciler diffs transcripts, finds the overturning contradiction.
- G6. Letter-writer drafts a verbatim, audit-grade appeal PDF; a human nurse signs.
- G7. Live dashboard streams the swarm, PAVO particles, the cost ticker (PAVO vs frontier), Moss cards, the contradiction, the letter.
- G8. All 7 sponsors load-bearing, each visibly in the loop.

### Non-goals (explicitly out of scope for the hackathon)
- N1. Dialing real payers. We use a **sandbox IVR/rep simulator**.
- N2. Real PHI. All denial data is synthetic.
- N3. Autonomous filing. Human-in-the-loop sign-off is mandatory.
- N4. Real EHR/clearinghouse/payer-portal integrations. Stubbed with clean interfaces.
- N5. Production auth, multi-tenant isolation, billing. Single-tenant demo.
- N6. Retraining PAVO. We use the released weights + the coupling mask (verified).

---

## 3. Architecture

```
                         ┌────────────────────────────────────────────┐
   denial.pdf  ──upload──▶│  INTAKE (Unsiloed parse → AppealSpec)        │
                         └───────────────┬────────────────────────────┘
                                         │ AppealSpec
                                         ▼
                         ┌────────────────────────────────────────────┐
                         │  SWARM ORCHESTRATOR                          │
                         │  concierge → denial-code gates → specialists │
                         └───────────────┬────────────────────────────┘
                       fan-out (parallel) │
        ┌────────────────────────────────┼────────────────────────────────┐
        ▼                                ▼                                ▼
 ┌─────────────┐                 ┌─────────────┐                  ┌─────────────┐
 │ provider-   │                 │ prior-auth  │                  │ records-    │   each agent =
 │ line agent  │                 │ desk agent  │                  │ desk agent  │   a Call loop
 └──────┬──────┘                 └──────┬──────┘                  └──────┬──────┘
        │  per turn:                    │                                │
        │  1. IVR/rep utterance (LiveKit transport ↔ sandbox IVR sim)
        │  2. SignalExtractor → 12-dim state (complexity, SNR, ctx…)
        │  3. PAVO masked router → profile → model tier
        │  4. Moss retrieval (IVR path / rebuttal / precedent)
        │  5. MiniMax (mid) or local tier composes the agent's line; Qwen TTS
        │  6. TrueFoundry gateway logs cost + audit + PHI redaction
        └──────────────────────────────┬────────────────────────────────┘
                                        ▼  transcripts
                         ┌────────────────────────────────────────────┐
                         │  RECONCILER  (find the overturning contradiction) │
                         └───────────────┬────────────────────────────┘
                                         ▼
                         ┌────────────────────────────────────────────┐
                         │  LETTER-WRITER → appeal.pdf  → NURSE SIGN     │
                         └────────────────────────────────────────────┘

   All events ──▶ WebSocket ──▶ DASHBOARD (swarm viz, PAVO particles, cost ticker,
                                            Moss cards, contradiction, letter drop)
   Runs on AWS. PAVO weights vendored. TrueFoundry is the gateway for every model call.
```

---

## 4. Module boundaries (Python package `backstop`)

| Module | Responsibility | Sponsor |
|---|---|---|
| `pavo/model.py` | `MetaController` (vendored, unchanged) | PAVO |
| `pavo/router.py` | `MaskedPAVORouter`: state → coupling-mask → argmax → `Profile`; `Profile → ModelTier` map | PAVO |
| `pavo/signal.py` | `SignalExtractor`: a `CallTurn` → 12-dim state vector (complexity, SNR, ctx) | PAVO |
| `models.py` | domain dataclasses: `Denial`, `AppealSpec`, `CallTurn`, `RouteDecision`, `Rebuttal`, `Contradiction`, `AppealLetter`, `SwarmEvent` | — |
| `swarm.py` | `Concierge`, `Specialist`, `pick_specialists` (denial-code gates), `run_swarm` (async fan-out) | — |
| `call.py` | `run_call(agent, ivr)`: the per-turn loop (transport → signal → route → retrieve → compose → TTS → cost) | LiveKit |
| `ivr_sim.py` | `SandboxIVR`: scripted payer IVR + rep, deterministic, with a planted contradiction | (sim) |
| `reconcile.py` | `find_contradiction(transcripts)` via Moss semantic diff | Moss |
| `letter.py` | `draft_appeal(spec, contradiction, transcripts)` → verbatim PDF | (Unsiloed-adjacent) |
| `cost.py` | `CostLedger`: per-turn PAVO cost vs frontier-baseline cost; the ticker source | TrueFoundry |
| `integrations/moss.py` | `MossClient.retrieve(query, kind)` — IVR path / rebuttal / precedent; seeded corpus | Moss |
| `integrations/livekit_client.py` | concurrent call transport; nurse bridge (stub-or-real) | LiveKit |
| `integrations/truefoundry.py` | `gateway(model, prompt)` — routes model calls, PHI redaction, audit log, cost ledger feed | TrueFoundry |
| `integrations/unsiloed.py` | `parse_eob(file) → AppealSpec` | Unsiloed |
| `integrations/minimax.py` | mid-tier reasoning + multilingual completion | MiniMax |
| `integrations/qwen.py` | brand-voice TTS (one "appeals coordinator" voice), multilingual | Qwen |
| `integrations/aws.py` | burst-container abstraction (local async pool standing in for Fargate) | AWS |
| `server.py` | FastAPI: `POST /appeals`, `GET /appeals/{id}`, `WS /stream`, static dashboard | — |

**Integration contract:** every `integrations/*` client exposes a real implementation when its key/env is present, and a deterministic **stub** when not. Each returns a `mode: "real" | "sim"` flag that the dashboard surfaces, so nothing is misrepresented as live.

---

## 5. Data models (authoritative shapes)

```python
# models.py  (Python 3.11, dataclasses; server uses Pydantic mirrors)

DenialCode = Literal["CO-16", "CO-197", "CO-50", "CO-11", "CO-45", "PR-1", "OTHER"]
ModelTier  = Literal["local_fast", "mid_reason", "frontier"]   # PAVO targets
SpecialistKind = Literal[
    "provider_line", "billing_office", "records_desk",
    "prior_auth_desk", "pharmacy_pbm", "reconciler", "letter_writer",
]

@dataclass
class Denial:
    denial_id: str
    payer: str                 # "Aetna"
    plan: str                  # "Aetna Choice POS II"
    state: str                 # "TX"
    denial_code: DenialCode
    cpt: list[str]             # ["99285"]
    billed_amount: float
    date_of_service: date
    member_id: str
    claim_id: str
    rendering_npi: str
    billing_npi: str
    raw_text: str              # original EOB text (verbatim, frozen)

@dataclass
class AppealSpec:              # Unsiloed output → swarm input
    denial: Denial
    required_specialists: list[SpecialistKind]
    sol_deadline: date         # appeal filing deadline
    parse_confidence: float

@dataclass
class CallTurn:
    turn_id: int
    speaker: Literal["agent", "rep", "ivr"]
    text: str
    complexity: int            # 1..5  (drives the coupling mask)
    snr_db: float              # noisy line → mask cheap ASR
    ctx_tokens: int
    is_denial_reason: bool     # the "magenta flare" turns

@dataclass
class RouteDecision:           # one per turn, from MaskedPAVORouter
    turn_id: int
    profile_idx: int           # 0..47
    tier: ModelTier
    feasible_count: int        # how many of 48 profiles survived the mask
    latency_factor: float
    pavo_cost_usd: float
    frontier_cost_usd: float   # counterfactual for the ticker

@dataclass
class Rebuttal:                # Moss retrieval result
    denial_code: DenialCode
    payer: str
    magic_words: str           # "ask for auth lookup by RENDERING NPI"
    win_rate: float            # 0..1, from the corpus
    source_id: str             # runbook citation

@dataclass
class Contradiction:
    claim: str                 # rep said "no auth on file"
    evidence: str              # records desk read auth #A4471
    rep_turn_id: int
    evidence_turn_id: int

@dataclass
class AppealLetter:
    appeal_id: str
    body_md: str               # quotes reps verbatim w/ timestamps
    citations: list[str]
    pdf_path: str
    signed_by_nurse: bool
```

### WebSocket event contract (`WS /stream`)

All events are JSON `{ "type": ..., "appeal_id": ..., "ts": ..., ...payload }`.

| `type` | payload | dashboard effect |
|---|---|---|
| `intake.parsed` | `AppealSpec` | spec card fills (denial code, CPT, amount) |
| `swarm.spawn` | `{ specialists: [{id, kind, label}] }` | cells burst from the PAVO singularity |
| `call.turn` | `{ agent_id, turn: CallTurn }` | transcript line streams under the cell |
| `pavo.route` | `RouteDecision` | a particle (mint=local, magenta=frontier on denial-reason) |
| `moss.hit` | `Rebuttal` | a Moss card flashes on the calling cell |
| `cost.tick` | `{ pavo_total, frontier_total, ratio }` | the cost ticker updates |
| `reconcile.found` | `Contradiction` | red highlight diff of the two turns |
| `letter.ready` | `AppealLetter` | the appeal PDF drops |
| `sponsor.mode` | `{ name, mode }` | sponsor row shows real/sim badge |

### REST contract
- `POST /appeals` (multipart: `denial` file or `denial_id` of a seeded sample) → `{ appeal_id }`. Kicks off the async pipeline; events stream over WS.
- `GET /appeals/{id}` → full `Appeal` snapshot (for reload / fallback replay).
- `GET /samples` → seeded synthetic denials for the demo dropdown.

---

## 6. PAVO routing contract (the verified core)

`MaskedPAVORouter.route(state: np.ndarray[12]) -> RouteDecision`

1. `logits, _ = model(state)`  (released `MetaController`, constant policy)
2. Build profiles: `profile[i].max_complexity = min(1 + int(i/48*5), 5)`, `latency_factor = 0.5 + (i/48)*2.0`.
3. `mask = feasible_mask(complexity = round(state[10]*5), snr = state[0]*50)`:
   - `complexity > profile.max_complexity` → infeasible
   - `snr < 10 and profile.quality < 0.8` → infeasible
   - if all masked → cloud fallback (last profile)
4. `profile_idx = argmax(where(mask, logits, -inf))`
5. Map `profile_idx → tier`: `<2 → local_fast`, `<29 → mid_reason`, `else → frontier`.
6. Cost: `pavo_cost = TIER_PRICE[tier] * tokens`; `frontier_cost = TIER_PRICE['frontier'] * tokens`.

Verified behavior (test in §9): complexity 1→5 escalates profile 2→15→20→30→40, latency_factor 0.58x→2.17x. This is the demand-conditioned routing and the cost collapse. **Without the mask the router is constant — the mask is load-bearing and must be covered by a test.**

---

## 7. Real-vs-Simulated matrix (honesty contract)

| Component | Demo mode | Real path (post-hackathon) |
|---|---|---|
| PAVO router + weights | **REAL** (vendored weights, masked) | same |
| Cost ledger / ratio | **REAL** (computed from real token counts × tier prices) | same |
| Unsiloed EOB parse | REAL if `UNSILOED_API_KEY`, else **deterministic parser** on synthetic EOBs | real API |
| Moss retrieval | REAL if `MOSS_API_KEY`, else **local embedded retriever** over the seeded corpus | real Moss runtime |
| MiniMax / Qwen | REAL if keys, else **canned-but-scripted** completions/TTS | real APIs |
| LiveKit transport | REAL room if keys, else **in-proc audio sim** | real concurrent calls |
| TrueFoundry gateway | REAL if key, else **local gateway shim** (still logs audit + cost) | real gateway |
| Payer IVR/rep | **SANDBOX SIM ALWAYS** (no real payers, by policy) | provider-line dialing |
| Denial data | **SYNTHETIC ALWAYS** (no PHI) | BAA'd real claims |
| Nurse sign-off | **SIM toggle** in dashboard | real nurse UI |

Every sponsor renders its `real|sim` badge live. Nothing claims to be real that isn't.

---

## 8. 8-hour build order (front-load the demo spine)

| Hr | Milestone | Output / verify |
|---|---|---|
| H1 | Repo + vendored PAVO + **MaskedPAVORouter** + signal extractor | `pytest test_router.py` shows complexity 1→5 escalation |
| H2 | Domain models + `ivr_sim.py` (3 sandbox IVRs w/ planted contradiction) | unit: a scripted call yields turns + the contradiction |
| H3 | `call.py` per-turn loop wired to router + cost ledger | a single call prints per-turn tier + running cost ratio |
| H4 | `swarm.py` fan-out + denial-code gates + Moss retriever (seeded corpus) | 3 concurrent calls; Moss card fires on the denial-reason turn |
| H5 | `server.py` FastAPI + WS event stream + REST | `curl POST /appeals` → events over `WS /stream` |
| H6 | Dashboard: swarm viz, PAVO particles, **cost ticker**, Moss cards | open browser → see the swarm + ticker move on a real run |
| H7 | Reconciler + letter-writer + PDF drop + nurse sign toggle | letter PDF quotes the rep verbatim; contradiction highlighted |
| H8 | All-7 sponsor badges + MiniMax/Qwen/TrueFoundry wiring + rehearse 90s + recorded fallback | `sponsor.mode` shows 7 badges; demo runs twice clean |

**The spine that must work even if everything else slips:** H1→H3→H5→H6 = a single call, routed by real PAVO, with the live cost ticker, on the dashboard. That alone proves the thesis.

---

## 9. Acceptance criteria (pass/fail)

1. `MaskedPAVORouter` routes complexity 1 and complexity 5 to **different** tiers (test asserts profile 1<5 escalation, latency_factor increases).
2. Without the mask, the router returns a **constant** profile (test asserts the failure mode, documents why the mask exists).
3. `POST /appeals` with a seeded denial emits, over WS, in order: `intake.parsed` → `swarm.spawn` → ≥1 `call.turn` → ≥1 `pavo.route` → ≥1 `moss.hit` → `cost.tick` → `reconcile.found` → `letter.ready`.
4. The cost ticker `ratio` is computed from real token counts and is **≥3.0x** on a full call (cheap turns dominate).
5. At least one `pavo.route` event on a `is_denial_reason` turn has `tier == "frontier"` (a magenta flare); the majority of turns are `local_fast` (mint).
6. The generated appeal PDF contains a verbatim rep quote with a timestamp and the retrieved rebuttal's `magic_words`.
7. The dashboard renders all 7 sponsor badges with a `real|sim` mode each.
8. The full demo runs end-to-end from the seeded denial in < 90s, twice, deterministically (sandbox scripts).

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Live voice flakes on stage | Sandbox IVR is deterministic + recorded fallback of the same run |
| Sponsor API key missing | Every integration has a clean sim mode + visible badge |
| PAVO constant-policy surprise | Mask is the documented fix; test covers both masked + unmasked |
| 8h overrun | The spine (H1→H3→H5→H6) is demoable on its own; H7-H8 are additive |
| "Is it real?" challenge | The real-vs-sim matrix is shipped in the README and shown as badges |

---

## 11. Repo layout

```
backstop/
  README.md                      # pitch + how to run + sponsor map
  docs/SPEC.md                   # this file
  docs/DEMO_SCRIPT.md            # the 90-second runbook
  docs/ARCHITECTURE.md           # diagrams + module contracts
  pyproject.toml
  orchestrator/
    backstop/
      __init__.py
      models.py
      swarm.py  call.py  reconcile.py  letter.py  cost.py  ivr_sim.py  server.py
      pavo/ model.py  router.py  signal.py  weights/*.pt
      integrations/ moss.py livekit_client.py truefoundry.py unsiloed.py minimax.py qwen.py aws.py
    tests/ test_router.py test_swarm.py test_pipeline.py
  data/
    denials/*.json               # synthetic EOBs
    runbooks/*.md                # payer rebuttal corpus (Moss seed)
    ivr_scripts/*.yaml           # sandbox IVR trees + planted contradictions
  web/                           # dashboard (Vite + React or static + WS)
  scripts/ seed_moss.py run_demo.py
```
