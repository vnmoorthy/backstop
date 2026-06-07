# Backstop — Architecture

> Engineer-facing companion to `docs/SPEC.md`. The system diagram, module map, the
> WebSocket event table, the end-to-end data flow, and the real-vs-sim matrix.
> Powered by the PAVO router (TMLR 2026).

---

## 1. System diagram

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

The **spine** — the part that proves the thesis on its own — is a single call, routed by real
PAVO, with the live cost ticker, on the dashboard: `intake → call loop (PAVO route + cost) → WS → dashboard`.
Everything else (swarm fan-out, reconciler, letter, nurse sign) is additive around that spine.

---

## 2. Module map (Python package `backstop`)

| Module | Responsibility | Sponsor |
|---|---|---|
| `pavo/model.py` | `MetaController` (vendored TMLR weights, 85,041 params, unchanged) | PAVO |
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

**Integration contract.** Every `integrations/*` client exposes a real implementation when its
key/env is present and a deterministic **stub** when not. Each returns a `mode: "real" | "sim"`
flag the dashboard surfaces, so nothing is misrepresented as live. The payer IVR is **always sim**
and denial data is **always synthetic**, by policy — those two never flip to real.

---

## 3. WebSocket event table (`WS /stream`)

All events are JSON `{ "type": ..., "appeal_id": ..., "ts": ..., ...payload }`, emitted in
pipeline order by `server.py`.

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

**Guaranteed ordering for a seeded run:**
`intake.parsed` → `swarm.spawn` → ≥1 `call.turn` → ≥1 `pavo.route` → ≥1 `moss.hit` →
`cost.tick` → `reconcile.found` → `letter.ready`. At least one `pavo.route` on an
`is_denial_reason` turn carries `tier == "frontier"` (the magenta flare); the majority of turns
are `local_fast` (mint). The dashboard also supports a **replay fallback**: if the socket is down,
the Run button plays a canned, identically-ordered sequence so the demo survives a dead network.

### REST contract

- `POST /appeals` (multipart: `denial` file or `denial_id` of a seeded sample) → `{ appeal_id }`. Kicks off the async pipeline; events stream over `WS /stream`.
- `GET /appeals/{id}` → full `Appeal` snapshot (for reload / fallback replay).
- `GET /samples` → seeded synthetic denials for the demo dropdown.

---

## 4. Data flow

```
denial.pdf
   │
   ▼  integrations/unsiloed.parse_eob()                      → emit intake.parsed
AppealSpec { denial, required_specialists, sol_deadline, parse_confidence }
   │
   ▼  swarm.Concierge + pick_specialists(denial_code)        → emit swarm.spawn
[ provider_line, prior_auth_desk, records_desk ]   (denial-code gates select the set)
   │
   ▼  swarm.run_swarm() — async fan-out over integrations/aws burst pool
   │
   │   per agent, per turn — call.run_call(agent, ivr):
   │     1. ivr_sim utterance over integrations/livekit_client  → emit call.turn
   │     2. pavo.signal.SignalExtractor → 12-dim state
   │     3. pavo.router.MaskedPAVORouter.route(state)          → emit pavo.route
   │            • coupling mask drops profiles that can't serve the turn's complexity
   │            • argmax over feasible logits → profile_idx → ModelTier
   │     4. integrations/moss.retrieve(query, kind)            → emit moss.hit (on denial-reason)
   │     5. compose line via integrations/minimax (mid) or local tier; integrations/qwen TTS
   │     6. integrations/truefoundry.gateway() → PHI redact + audit + cost.CostLedger
   │                                                            → emit cost.tick (PAVO vs frontier ratio)
   │
   ▼  transcripts (per agent)
reconcile.find_contradiction(transcripts) via Moss semantic diff  → emit reconcile.found
Contradiction { claim, evidence, rep_turn_id, evidence_turn_id }
   │
   ▼  letter.draft_appeal(spec, contradiction, transcripts) → appeal.pdf  → emit letter.ready
AppealLetter { body_md (verbatim rep quotes + timestamps), citations, pdf_path, signed_by_nurse }
   │
   ▼  NURSE SIGN  (dashboard toggle — never autonomous)
```

**PAVO routing contract (the verified core).** `MaskedPAVORouter.route(state) -> RouteDecision`:
the released `MetaController` produces logits under a constant policy; the **coupling mask** is what
makes routing demand-conditioned. For a turn of complexity *C*, profiles whose `max_complexity < C`
(or whose quality can't survive a low-SNR line) are masked to `-inf` before the argmax, so the chosen
tier escalates with the turn. Verified escalation: complexity 1→5 moves profile 2→15→20→30→40,
latency_factor 0.58x→2.17x. **Without the mask the router returns a constant profile** — the mask is
load-bearing and is covered by `tests/test_router.py` (both the masked escalation and the
unmasked-constant failure mode).

**Cost math.** Per turn, `pavo_cost = TIER_PRICE[tier] * tokens` and the counterfactual
`frontier_cost = TIER_PRICE['frontier'] * tokens`. Because ~90% of turns route to the near-free local
tier and frontier is spent only on the 2–3 denial-reason turns, the ledger lands at the verified
ratios: **provider call 5.4x, full appeal 4.4x cheaper (PAVO $0.024 vs frontier $0.105)**. Stress test:
100 concurrent appeals (~300 agents), 100/100 succeed, avg 4.40x, 2.4s, 0 errors.

---

## 5. Real-vs-sim matrix (honesty contract)

| Component | Demo mode | Real path (post-hackathon) |
|---|---|---|
| PAVO router + weights | **REAL** (vendored TMLR weights, 85,041 params, masked) | same |
| Cost ledger / ratio | **REAL** (computed from real token counts × tier prices) | same |
| Unsiloed EOB parse | REAL if `UNSILOED_API_KEY`, else **deterministic parser** on synthetic EOBs | real API |
| Moss retrieval | REAL if `MOSS_API_KEY`, else **local embedded retriever** over the seeded corpus | real Moss runtime |
| MiniMax / Qwen | REAL if keys, else **canned-but-scripted** completions / TTS | real APIs |
| LiveKit transport | REAL room if keys, else **in-proc audio sim** | real concurrent calls |
| TrueFoundry gateway | REAL if key, else **local gateway shim** (still logs audit + cost) | real gateway |
| AWS burst | local async pool standing in for Fargate | real elastic containers |
| Payer IVR / rep | **SANDBOX SIM ALWAYS** (no real payers, by policy) | provider-line dialing |
| Denial data | **SYNTHETIC ALWAYS** (no PHI) | BAA'd real claims |
| Nurse sign-off | **SIM toggle** in dashboard | real nurse UI |

Every sponsor renders its `real | sim` badge live over `sponsor.mode`. The two policy locks — sandbox
IVR and synthetic data — never flip to real in this product; live calling and real claims are the
post-hackathon path behind a BAA. Nothing claims to be real that isn't.

---

## 6. Where to look next

- `docs/SPEC.md` — full engineering spec, data models, acceptance criteria, 8-hour build order.
- `docs/DEMO_SCRIPT.md` — the 90-second runbook.
- `data/runbooks/*.md` — the seeded payer-rebuttal corpus Moss retrieves over (e.g. `aetna_co197.md`).
- `orchestrator/tests/test_router.py` — the masked-escalation + unmasked-constant proof.
