# Backstop — Product Requirements Document

**Status:** For review (pre-implementation) · **Owner:** vnmoorthy · **Date:** 2026-06-07
**Companion docs:** [`SYSTEM_DESIGN.md`](./SYSTEM_DESIGN.md) · [`BUILD_PLAN.md`](./BUILD_PLAN.md) · [`ARCHITECTURE_CANON.json`](./ARCHITECTURE_CANON.json)

---

## 1. Summary & vision

Backstop is a **voice-agent swarm that recovers denied insurance claims by winning the phone appeal.** A denial detonates into AI agents that (in production) call the payer's provider line, billing office, and records desk in parallel, navigate IVRs and holds, retrieve the exact rebuttal in real time, find the contradiction that overturns the denial, and hand a human appeals nurse a verbatim, audit-grade appeal letter to sign and file.

The **moat** is the compounding outcome corpus — every recovered dollar makes the rebuttal retrieval, the payer playbooks, and the PAVO routing policy smarter — and the **PAVO masked router** (a real TMLR-published model) that collapses per-call model cost ~4.4× versus all-frontier inference.

This PRD covers the **real-engineering rebuild**: replace the demo-grade prototype (decorative integrations, in-memory state, no auth) with a genuinely engineered, HIPAA-aware, test-gated product where all 8 sponsors are load-bearing.

## 2. Problem & target users

Mid-market hospital billing departments and outsourced RCM firms write off enormous sums in denied claims because appealing is manual, slow, and expensive: a nurse re-keys data across the EHR, payer portals, and spreadsheets, sits on hold 14+ minutes per call, and re-argues each denial from scratch. A large share of denials are never reworked, and a majority of those that would have been overturned are simply abandoned.

| User | Job they hire Backstop for |
|---|---|
| **RCM / denials ops leader** | Recover the written-off backlog; raise overturn rate; cut cost-to-collect |
| **Appeals nurse** | A prioritized worklist + a verifiable, ready-to-sign appeal letter she'll put her license behind |
| **CFO / finance** | Auditable recovered-dollars attribution for contingency billing and board reporting |
| **Compliance / privacy officer** | Proof that PHI never leaves a BAA boundary; a tamper-evident audit trail |

## 3. Jobs-to-be-done
1. Turn a messy pile of denials (835 ERA / EOB images / 277CA) into a structured, prioritized worklist.
2. Decide per denial: **resubmit vs. appeal vs. peer-to-peer** (don't burn the timely-filing clock).
3. Work the appeal — retrieve the winning rebuttal, find the overturning contradiction, draft the verbatim letter.
4. Get a credentialed human sign-off that is an enforced compliance gate, not a checkbox.
5. Prove every recovered dollar to finance and to a payer/RAC audit.

## 4. Goals & non-goals

**Goals**
- Genuinely real engineering: hexagonal architecture, one responsibility per file, every capability behind a port with a real adapter **and** a real-work sim adapter; a unit/contract test gate after every interface.
- All 8 sponsors load-bearing in the live data path.
- Fix **all 24 audited vulnerabilities** (6 Critical, 15 High, plus Medium/Low), each with a 1:1 regression test.
- Lay the substrate for the **NOW** feature bucket (ingestion, triage, routing, nurse review + sign-off).

**Non-goals (this build)**
- Placing **real** outbound calls to real payer lines (sandbox IVRs only — the correct legal posture; telephony + BAAs are a later phase).
- EHR write-back, multi-tenant white-label, online PAVO finetuning (roadmap "Later").
- Multi-node horizontal scale (SQLite single-node default; Postgres swap is free via the port — see Open Question 5).

## 5. Success metrics
| Metric | Target (this build) |
|---|---|
| Sponsors load-bearing & honest (real/sim badge) | 8 / 8 |
| Critical/High vulns fixed with a regression test | 21 / 21 |
| PAVO cost-collapse ratio vs all-frontier | ≥ 4× (currently 4.4×) |
| Per-turn routing on the redacted side of the PHI boundary | 100% |
| Test gate green before each next interface | enforced (CI) |
| Unit + contract + e2e + load + security suites | all green; branch-coverage gate on domain/services |
| End-to-end appeal (upload → swarm → reconcile → sign → FILED) | passes in sim, synthetic PHI |

## 6. Scope

**In scope:** the greenfield hexagonal rebuild under `orchestrator/backstop/` (domain → ports → adapters → services → controllers → composition), SQLite persistence, auth/RBAC, structural PHI redaction boundary, tamper-evident audit, the 8 sponsor adapter pairs, the per-turn PAVO loop + concurrent swarm, the reconcile/letter pipeline, the NOW-substrate services (ingestion/triage/route/review/sign-off), Docker + CI, and the full test suite.

**The 4 NOW features** (substrate built here, full UX iterated next): batch backlog ingestion (835/837/277 + EOB-image), recoverable-$ × SOL triage worklist, resubmit-vs-appeal-vs-peer routing, nurse review queue + per-appeal evidence timeline + cryptographic sign-off gate.

## 7. Sponsor usage — all 8 load-bearing

| Sponsor | Capability | Port(s) | Real adapter | Sim adapter (real local work) |
|---|---|---|---|---|
| **PAVO** | per-turn model-tier routing (the cost collapse) | `RoutingPort` | torch on vendored TMLR weights | numpy, **bit-faithful** (0/2000 argmax mismatch) |
| **Moss** | real-time rebuttal/precedent retrieval | `RetrievalPort` | `POST /v1/query` project-scoped | real TF-IDF+cosine over runbook corpus |
| **TrueFoundry** | LLM gateway + PHI redaction + audit + cost | `LLMGatewayPort` + `RedactionPort` + `AuditLogPort` + `CostLedgerPort` | OpenAI-compatible gateway | real redaction + hash-chain audit + priced ledger |
| **Unsiloed** | EOB/CMS-1500/UB-04/835 parsing | `DenialParserPort` | async extract+poll | real X12 + EOB deterministic parser |
| **MiniMax** | mid-tier reasoning / compose spoken line | `ReasoningPort` | `chatcompletion_v2` | real grounded-NLG slot-filling (no echo) |
| **Qwen/DashScope** | brand-voice TTS | `SpeechSynthesisPort` | DashScope TTS | real stdlib DSP → valid playable WAV |
| **LiveKit** | voice transport + nurse barge-in | `VoiceTransportPort` | LiveKit SDK, real HS256 token mint | real in-process asyncio pub/sub transport |
| **AWS** | elastic burst concurrency cap | `ConcurrencyGatePort` | Fargate warm-pool RunTask | real `asyncio.Semaphore` backpressure |

Every sim adapter does **genuine local work** and is the test double its real twin is contract-tested against, so real and sim provably honor the same port.

## 8. Functional requirements
- **FR1 Ingestion:** accept EOB (PDF/image), CMS-1500/UB-04, and raw X12 835/837/277; normalize to a canonical `Denial` with per-field provenance; route raw EDI to the deterministic parser.
- **FR2 Triage:** score each denial on recoverable-$ × SOL urgency; produce a ranked worklist with deadline timers.
- **FR3 Route:** classify resubmit vs. appeal vs. peer-to-peer from CARC/RARC + claim context.
- **FR4 Swarm:** fan out specialist calls concurrently under a concurrency gate; per turn, route via PAVO → (on denial turn) retrieve via Moss → compose via MiniMax through the TrueFoundry gateway → synthesize via Qwen → transport via LiveKit.
- **FR5 Reconcile:** find the contradiction overturning the denial across desks.
- **FR6 Letter:** draft a verbatim, redacted, markup-safe appeal-letter PDF.
- **FR7 Review & sign-off:** nurse queue + per-appeal evidence timeline (every letter sentence linked to its source); FILED is unreachable without a valid Ed25519 signature over the redacted-letter hash **and** a verified audit chain.
- **FR8 Cost/observability:** live PAVO-vs-frontier cost ledger; real/sim badges; structured logs.

## 9. Non-functional requirements
- **Security/HIPAA:** auth on every endpoint + RBAC; structural PHI-redaction boundary (`RedactedText` type); BAA-gated routing (no-BAA vendors only on the redacted side); tamper-evident audit; signed-URL file access; CORS allowlist + CSP + security headers; no server-side path opened from user input.
- **Performance:** async hot path; per-appeal concurrency slot; repo lock held only across in-memory/row mutation, never across an await — the swarm is genuinely parallel.
- **Scalability:** bounded memory (LRU+TTL sim repo / SQLite WAL); concurrency gate models burst-to-N / idle-to-zero.
- **Reliability:** graceful shutdown drains tracked tasks; atomic compare-and-set status transitions; PDF/event TTL sweep.
- **Maintainability:** one responsibility per file; layering enforced by a static architecture-guard test; `mypy --strict`, `ruff`, pinned deps.

## 10. Honesty & compliance constraints
- The swarm does **not** dial real payers (sandbox IVRs, synthetic PHI, mandatory nurse sign-off). Stated in-product; a credibility asset.
- Every integration surfaces its real/sim mode; nothing is misrepresented as live.
- PAVO weights/policy are **frozen** — adapters wrap inference only (never `.train()`), preserving the published result.

## 11. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Live sponsor API contracts uncertain (Moss/TrueFoundry/Unsiloed/MiniMax) | All vendor I/O isolated in one adapter file; base URL + path + auth env-configurable; real adapters validated via mocked HTTP; sim adapters keep the pipeline running offline |
| PHI leak | Made a **type error** at every egress boundary + runtime defense-in-depth + hypothesis-fuzzed redaction |
| Hidden regressions during rebuild | Test gate after every interface; 24 named security regression tests; CI lanes (ruff/mypy/pytest/load) |
| Torch-less CI hosts | numpy PAVO adapter is bit-faithful and torch-free |

## 12. Milestones & acceptance criteria
19 test-gated milestones **M0–M18** (full detail in [`BUILD_PLAN.md`](./BUILD_PLAN.md)): M0 scaffold + architecture guard → M1 domain kernel → M2–M13 the 8 sponsor + infra ports/adapters (each with a contract test) → M14–M16 services → M17 composition + controllers + middleware → M18 the 24-finding security regression suite + e2e + load.

**Definition of done:** every milestone's test gate green; all 24 security tests pass; e2e appeal reaches FILED in sim with synthetic PHI; `ruff` / `mypy --strict` / `bandit` / `pip-audit` clean; PAVO ratio ≥ 4×; all 8 sponsor badges resolve real-or-sim honestly.

## Open questions for review
See [`SYSTEM_DESIGN.md` §13](./SYSTEM_DESIGN.md). Headlines: (1) Moss live auth header shape, (2) TrueFoundry gateway base URL/path for tenant `hackathon12`, (3) Unsiloed EDI handling, (4) MiniMax default model + GroupId, (5) SQLite vs Postgres now, (6) runbook corpus coverage, (7) sim voice realism, (8) commit PAVO `.npz` for torch-free CI.
