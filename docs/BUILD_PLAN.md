# Backstop — Build Plan (test-gated)

**Status:** For review (pre-implementation) · **Companion:** [`PRD.md`](./PRD.md) · [`SYSTEM_DESIGN.md`](./SYSTEM_DESIGN.md)

> **The rule:** build ONE interface (one file, one responsibility) → write its unit/contract tests → run them → **GREEN before the next step.** Never proceed on red. Each adapter pair is contract-tested against its port so real and sim provably behave identically.

## The per-step test-gate ritual (repeat every step)
1. Write the port/interface (or the single function) — one responsibility.
2. Write its tests **first or alongside** (unit; contract for adapters; service tests use sim/fake ports as doubles).
3. Run the gate: `pytest <that test file> -q` → must pass.
4. Run the guards: `ruff check <file>` + `mypy --strict <module>` → clean.
5. **Do not proceed until green.** Commit the step.

**CI lanes (run on every push):** `ruff` → `mypy --strict` → `pytest` (unit/contract/services/security) → load smoke → `bandit -ll` + `pip-audit`.

**Tooling (pinned in M0):** pytest 8, pytest-asyncio (mode=auto), pytest-cov (branch), pytest-httpx, hypothesis, freezegun, ruff, mypy, bandit, pip-audit.

---

## Milestone dependency order
```
M0 → M1 → ┬ M2 (PAVO)            ┐
          ├ M3 (redaction) → M4 (audit+cost) → M5 (gateway)
          ├ M6 (corpus) → M7 (Moss) , M8 (Unsiloed)
          ├ M9 (MiniMax, needs M7) , M10 (Qwen, needs M3) , M11 (LiveKit) , M12 (AWS)
          └ M13 (persistence/filestore/eventbus/letter/signature/auth)
   M14 (CallService) → M15 (Swarm+Reconcile) → M16 (ingest/triage/appeal/letter/review/signoff)
   → M17 (composition + controllers + middleware) → M18 (security suite + e2e + load)
```

---

## M0 — Scaffold + composition skeleton + architecture guard
- **Build:** package tree (one `__init__.py` per layer), `pyproject.toml` (pinned dev stack, ruff + mypy config), `infra/config.py` (frozen pydantic-settings — the ONLY `os.environ` reader), `composition/container.py` skeleton.
- **Tests:** `tests/arch/test_layering.py` — static AST scan asserting (a) only `config.py` imports `os.environ`; (b) no service/domain imports a vendor SDK.
- **GATE:** `pytest tests/arch -q` PASS **and** `mypy --strict orchestrator/backstop` clean **and** `ruff check` clean.

## M1 — Domain kernel
- **Build:** `domain/` — `models.py`, `enums.py` (`AppealStatus` state machine), `redacted.py` (`RedactedText` NewType + `PhiSpan`), `money.py` (integer-cents `Money`/`RecoverableDollars`/`SolDeadline`), `errors.py`, `appeal_aggregate.py` (transitions; **cannot reach FILED without a signature event**), `triage.py` (pure recoverable-$ × SOL scoring), `routing_policy.py` (resubmit/appeal/peer rules), `carc_table.py`.
- **Tests:** `tests/unit/test_domain_*.py` — 100% line+branch on `triage`, `routing_policy`, `money`, `appeal_aggregate`; assert the state machine rejects `FILED` without a signature; assert `RedactedText` cannot be constructed from raw `str` except via the sanctioned path.
- **GATE:** `pytest tests/unit/test_domain_*.py -q --cov=backstop/domain --cov-branch` (branch gate met).

## M2 — PAVO `RoutingPort` + both bit-faithful adapters
- **Build:** `ports/routing_port.py`; `adapters/pavo/_coupling.py` (shared `turn_to_state_vector` + 48→3 collapse + feasibility); `torch_routing_adapter.py` (real); `numpy_routing_adapter.py` (sim/fallback); one-time `meta_controller_weights.npz` export script (see Open Q8).
- **Tests:** `tests/contract/test_routing_port_contract.py` parametrized over torch+numpy (torch skipped if absent): `tiers() == (CLOUD_PREMIUM, HYBRID_BALANCED, ONDEVICE_FAST)`; `route()` deterministic over 100 calls; `is_feasible(ONDEVICE_FAST, complexity≥4) == False`; numpy↔torch argmax parity on a fixed battery.
- **GATE:** `pytest tests/contract/test_routing_port_contract.py -q` GREEN (both impls).

## M3 — TrueFoundry `RedactionPort` + LocalRedactionAdapter (the PHI boundary)
- **Build:** `ports/redaction_port.py`; `adapters/truefoundry/local_redaction_adapter.py` (real regex+context scrubber — the sole `RedactedText` producer); `tf_redaction_adapter.py` (real-mode wrapper, falls back to local rules).
- **Tests:** `tests/unit/test_redaction.py` + **hypothesis fuzz** — a million-char fuzzed string leaves NO surviving member-ID/NPI/SSN/DOB/claim#/name/phone (closes "incomplete redact_phi").
- **GATE:** `pytest tests/unit/test_redaction.py -q` GREEN incl. fuzz.

## M4 — TrueFoundry `AuditLogPort` (hash chain) + `CostLedgerPort`
- **Build:** `ports/audit_log_port.py`, `ports/cost_ledger_port.py`; `hashchain_audit_adapter.py`, `cost_ledger_adapter.py`; `infra/db.py` (SQLite WAL schema: `audit`, `cost_ledger`).
- **Tests:** `test_audit_chain.py` — after N `append()`, each `prev_hash == sha256(prior)`; flipping any field → `verify_chain() == False`; rows store hashes not raw text. `test_cost_ledger.py` — usage {1000,500} prices via the table; `snapshot()` sums correctly.
- **GATE:** `pytest tests/unit/test_audit_chain.py tests/unit/test_cost_ledger.py -q` GREEN.

## M5 — TrueFoundry `LLMGatewayPort` + both adapters (single LLM chokepoint)
- **Build:** `ports/llm_gateway_port.py`; `tf_gateway_adapter.py` (real, OpenAI-compatible) + `sim_gateway_adapter.py` (sim) — both injected the SAME redaction/audit/cost singletons.
- **Tests:** `tests/contract/test_gateway_contract.py` (both): `complete()` redacts outbound (transport sees 0 raw PHI) AND inbound; audit chain verifies; cost computed (sim cost > 0, not faked). Real adapter via `httpx.MockTransport`.
- **GATE:** `pytest tests/contract/test_gateway_contract.py -q` GREEN.

## M6 — Seed corpora + shared `RunbookCorpus` TF-IDF util
- **Build:** `data/runbooks/*.md` (CO-197 prior-auth, CO-50/CO-45 medical-necessity, N130 plan-provision — with front-matter) + `data/carc_rarc.json`; `adapters/text/runbook_corpus.py` (stdlib TF-IDF + cosine; sklearn optional accelerator).
- **Tests:** `test_runbook_corpus.py` — "prior authorization not obtained" ranks the CO-197 chunk first with score > 0 and chunk text ≠ query (real ranking, not echo); "not medically necessary" ranks CO-50 first.
- **GATE:** `pytest tests/unit/test_runbook_corpus.py -q` GREEN.

## M7 — Moss `RetrievalPort` + both adapters
- **Build:** `ports/retrieval_port.py`; `moss_http_adapter.py` (real, project-scoped) + `tfidf_retrieval_adapter.py` (sim over `RunbookCorpus`).
- **Tests:** `tests/contract/test_retrieval_contract.py` (both; real via `httpx.MockTransport`): `retrieve()` returns ranked `EvidenceChunk`s, `len ≤ top_k`, scores in [0,1] descending, `source_mode ∈ {real,sim}`; no-match returns empty (not error); adapter raises domain `RetrievalError`, never `httpx`.
- **GATE:** `pytest tests/contract/test_retrieval_contract.py -q` GREEN.

## M8 — Unsiloed `DenialParserPort` + both adapters (Stage-0 ingestion engine)
- **Build:** `ports/denial_parser_port.py`; `unsiloed_http_adapter.py` (real async extract+poll, **bytes-only**) + `deterministic_parser_adapter.py` (sim: real X12 835/837/277 + EOB/CMS-1500 parser using `carc_table`).
- **Tests:** `tests/contract/test_parser_contract.py` (both): full `DenialExtraction` field set; each `ExtractedField` confidence in [0,1] + provenance; `needs_human_review` bool. `test_sim_edi_835_real_parse` — a synthetic 835 with CLP/CAS/NM1 segments parses to the right CARC/amounts. **Vuln fix:** no code path opens a server-side path from user input.
- **GATE:** `pytest tests/contract/test_parser_contract.py -q` GREEN.

## M9 — MiniMax `ReasoningPort` + both adapters
- **Build:** `ports/reasoning_port.py`; `minimax_adapter.py` (real) + `local_reasoning_adapter.py` (sim: **real grounded-NLG slot-filling**, never an echo).
- **Tests:** `tests/contract/test_reasoning_contract.py` (both): `compose_line` ≤ `max_words`; citations are a **subset of supplied evidence ids** (never fabricated); ungrounded → safe fallback; `interpret_denial` category/route in enums.
- **GATE:** `pytest tests/contract/test_reasoning_contract.py -q` GREEN.

## M10 — Qwen `SpeechSynthesisPort` + both adapters
- **Build:** `ports/speech_synthesis_port.py`; `qwen_tts_adapter.py` (real DashScope) + `sim_tts_adapter.py` (sim DSP).
- **Tests:** `tests/contract/test_tts_contract.py` (both): `synth()` returns a `wave.open()`-parseable RIFF/WAVE (`nchannels==1`, `sampwidth==2`, `framerate==req.sample_rate`, `nframes>0`, `len>1KB` — **anti-stub gate**); sim duration scales with text length.
- **GATE:** `pytest tests/contract/test_tts_contract.py -q` GREEN.

## M11 — LiveKit `VoiceTransportPort` + both adapters
- **Build:** `ports/voice_transport_port.py`; `livekit_adapter.py` (real, **HS256 token mint**) + `inprocess_transport_adapter.py` (sim, real asyncio pub/sub).
- **Tests:** `tests/contract/test_transport_contract.py` (both; real via mocked LiveKit API): `open_channel` returns a `Channel` with room + agent token + future `expires_at`; `bridge_nurse` on unknown channel → `ChannelNotFound`; `close_channel` idempotent (fixes transport leaks); minted JWT decodes with expected grant claims.
- **GATE:** `pytest tests/contract/test_transport_contract.py -q` GREEN.

## M12 — AWS `ConcurrencyGatePort` + both adapters
- **Build:** `ports/concurrency_gate_port.py`; `semaphore_gate.py` (sim) + `fargate_gate.py` (real).
- **Tests:** `tests/contract/test_gate_contract.py` (both; real via fake ECS client): acquiring `max` slots succeeds, the `(max+1)`th BLOCKS until a release; `acquire(timeout)` on full → `CapacityTimeout`; `slot()` releases in `finally` even on exception.
- **GATE:** `pytest tests/contract/test_gate_contract.py -q` GREEN.

## M13 — Persistence + FileStore + EventBus + Letter + Signature + Auth (+ Clock/IdGen/TaskSupervisor)
- **Build:** ports + adapters: `AppealRepository` (`sqlite_appeal_repo` + `memory_appeal_repo` LRU+TTL), `FileStore` (`local_filestore_adapter` jail+TTL + `s3_filestore_adapter`), `EventBus` (`ws_event_bus_adapter`, **RedactedText-only publish**), `LetterRender` (`reportlab_letter_adapter`, **markup-escaping**), `Signature` (`ed25519_signature_adapter`), `Auth` (`jwt_auth_adapter` + RBAC), `system/` Clock/IdGen/TaskSupervisor.
- **Tests:** per-port contract suites — repo `update_status_atomic` CAS rejects stale version + concurrency test (**snapshot-watchdog race fix**); `MemoryAppealRepo` evicts at capacity (10k inserts → bounded RSS via `tracemalloc`); `LocalFileStore` rejects path traversal + enforces TTL sweep + per-appeal ownership; `EventBus.publish` rejects a non-`RedactedText` payload (type/runtime); `LetterRender` escapes reportlab markup; Ed25519 sign/verify round-trips & rejects a tampered hash; `Auth` rejects missing/bad token and cross-appeal access.
- **GATE:** `pytest tests/contract/test_repo_contract.py tests/contract/test_filestore_contract.py tests/contract/test_eventbus_contract.py tests/contract/test_letter_contract.py tests/contract/test_signature_contract.py tests/contract/test_auth_contract.py -q` GREEN.

## M14 — `CallService` (per-turn PAVO loop use-case)
- **Build:** `services/call_service.py` (injected ports); `adapters/ivr/ivr_sim_adapter.py` + `ports/ivr_port.py`.
- **Tests:** `tests/services/test_call_service.py` (sim ports as doubles): `handle_turn` calls `route()` exactly once **before** any LLM/ASR/TTS; `CLOUD_PREMIUM` selects premium reasoning, `ONDEVICE_FAST` does **not** call `ReasoningPort` (cost collapse); on the denial turn it retrieves + composes through the gateway; every outbound text is `RedactedText`.
- **GATE:** `pytest tests/services/test_call_service.py -q` GREEN.

## M15 — `SwarmService` + `ReconcileService` (concurrent orchestrator)
- **Build:** `services/swarm_service.py`, `services/reconcile_service.py`.
- **Tests:** `test_swarm_uses_gate.py` (sim gate `max=2`, 5 appeals): never > 2 appeal bodies concurrent; every appeal acquires before its PAVO loop and releases in `finally` even on exception (`capacity().in_use == 0` at end). `test_reconcile.py`: finds the cross-desk contradiction for CO-197/CO-50/CO-16.
- **GATE:** `pytest tests/services/test_swarm_uses_gate.py tests/services/test_reconcile.py -q` GREEN.

## M16 — Ingestion + Triage + Appeal + Letter + Review + Signoff services
- **Build:** `services/` — `ingest_denial_service.py`, `ingestion_batch_service.py`, `triage_service.py`, `appeal_service.py`, `letter_service.py`, `review_service.py`, `signoff_service.py`, `nurse_bridge_service.py`, `auth_service.py`.
- **Tests:** per-service with fake ports: ingest concurrency-capped + audit-wrapped + EDI→sim fallback; batch splits multi-claim EDI + throttles by capacity; triage orders by recoverable-$ × SOL; `letter_service` redacts **before** render; **`signoff_service` refuses to FILE unless `verify_chain()==True` AND a valid signature** (the compliance gate); `review_service` exposes only redacted evidence.
- **GATE:** `pytest tests/services -q` GREEN (all service suites).

## M17 — Composition root + FastAPI controllers + middleware + lifespan
- **Build:** `composition/adapter_factory.py` + `wiring.py` + `app.py`; `controllers/*` (appeals, ingestion, review, triage, files, ws, schemas, dependencies); `infra/security_headers.py` + `infra/logging.py`.
- **Tests:** `tests/composition/test_wiring.py` — `container.resolve(AppealService)` fully wired, no missing binding; mode flags pick real vs sim; PAVO factory tries torch then numpy (singleton). `tests/controllers/*` — every route authn-gated; size caps; CORS allowlist; CSP headers present; WS origin+token handshake; `/files` only via signed URL + ownership.
- **GATE:** `pytest tests/composition tests/controllers -q` GREEN.

## M18 — Security regression suite (24 findings) + E2E + load
- **Build:** `tests/security/test_sec_*.py` (≥ 24 named tests + a meta-test asserting all 24 IDs present); `tests/e2e/test_e2e_appeal.py` (upload → swarm → review → sign → FILED, all sim, sandbox IVR, synthetic PHI); `tests/load/test_load_swarm.py`; Dockerfile + CI.
- **Tests/GATE:** all 24 security tests green (each maps 1:1 to a Critical/High finding); the meta-test fails CI if any is deleted; `bandit -ll` + `pip-audit` zero high-severity; e2e reaches FILED; load asserts PAVO ratio ≥ 4× under N-concurrent with bounded memory.
- **GATE:** `pytest tests/security tests/e2e tests/load -q` GREEN **and** `bandit -ll` + `pip-audit` clean.

---

## Vulnerability → milestone map (all 24)
| Finding | Fixed in |
|---|---|
| Arbitrary file read / path traversal (Critical) | M8 (bytes-only) + M13 (path-jail) |
| No auth on any endpoint (Critical) | M13 `AuthPort` + M17 controllers |
| `/files` serves PHI unauthenticated (Critical) | M13 `FileStore` signed-URL + M17 ownership |
| PHI streamed to WS unredacted (Critical) | M3 redaction + M13 `EventBus` RedactedText-only |
| PHI in cleartext PDFs (Critical) | M3 + M13 `LetterRender` |
| Wildcard CORS (Critical) | M17 `security_headers` allowlist |
| Unbounded APPEALS dict / OOM (High) | M13 SQLite + LRU/TTL memory repo |
| PDFs never cleaned (High) | M13 `FileStore.sweep_expired` |
| reportlab markup injection (High) | M13 `LetterRender` escaping |
| Fire-and-forget tasks (High) | M13 `TaskSupervisor` |
| No graceful shutdown (High) | M17 lifespan + `TaskSupervisor.drain` |
| Lock-scope serializes swarm (High) | M15 lock-free hot path |
| Threadpool starvation (High) | M14/M15 async-first |
| No concurrency cap (High) | M12 gate + M15 `slot()` |
| Unbounded uploads (High) | M17 size caps |
| No WS origin check (High) | M17 ws handshake |
| Unredacted GET /appeals/{id} (High) | M13 auth + M17 redacted DTOs |
| Incomplete redact_phi (High) | M3 hypothesis fuzz |
| DOM XSS (High) | M17 web output encoding |
| Snapshot-watchdog race (High) | M13 CAS `update_status_atomic` |
| No structured logging/config/validation (High) | M0 config + M17 logging + schemas |
| (Med/Low: SSRF, broad-except, future-not-awaited, WS set mutation, intake leak, CSP/SRI, stuck-running, healthz split, dep pinning, BAA) | M3/M5/M8/M13/M17 as noted |

## Definition of done
- [ ] Every milestone gate green; CI all lanes pass.
- [ ] 24/24 security tests pass; meta-test enforces presence.
- [ ] E2E appeal reaches FILED in sim with synthetic PHI + nurse signature + verified audit chain.
- [ ] PAVO cost ratio ≥ 4× under load with bounded memory.
- [ ] `ruff` / `mypy --strict` / `bandit` / `pip-audit` clean.
- [ ] All 8 sponsor badges resolve real-or-sim honestly; real adapters validated via mocked HTTP.
- [ ] `docs/` updated; `.env.example` lists every key.
