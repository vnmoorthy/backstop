# Backstop — System Design

**Status:** For review (pre-implementation) · **Companion:** [`PRD.md`](./PRD.md) · [`BUILD_PLAN.md`](./BUILD_PLAN.md) · [`ARCHITECTURE_CANON.json`](./ARCHITECTURE_CANON.json)

> This is a **greenfield rebuild** into a hexagonal structure. The current flat prototype (`server.py`, `swarm.py`, `integrations/*`) is the v1 it replaces. Verified environment: Python 3.9.6; torch 2.8.0 + numpy 2.0.2 installed; **sklearn not installed** (so the TF-IDF util is stdlib-only). PAVO numpy↔torch parity verified empirically: 0 argmax mismatches over 2000 states, max logit error 1.9e-6.

---

## 1. Architecture overview

Hexagonal (ports & adapters), layer-grouped, with one composition root. Strict one-way dependencies — a layer may import only layers below it; **domain imports nothing**; adapters never import services or controllers.

```
                          ┌─────────────────────────────────────────────┐
   HTTP / WebSocket  ───▶ │  L5 CONTROLLERS   (FastAPI edge, no logic)   │
                          └───────────────┬─────────────────────────────┘
                                          │ depends on (DI)
                          ┌───────────────▼─────────────────────────────┐
                          │  L4 SERVICES   (use-cases; one job each)     │
                          └───────────────┬─────────────────────────────┘
                                          │ depends on (interfaces only)
                          ┌───────────────▼─────────────────────────────┐
                          │  L2 PORTS  (Protocol/ABC + DTOs) ◀───────────┼──┐ implemented by
                          └───────────────┬─────────────────────────────┘  │
                                          │ uses                            │
                          ┌───────────────▼─────────────────────────────┐  │
                          │  L1 DOMAIN  (pure entities, value objects,   │  │
                          │  RedactedText, Money, triage/routing math)   │  │
                          └──────────────────────────────────────────────┘  │
                          ┌──────────────────────────────────────────────┐  │
   vendor SDKs / HTTP ◀── │  L3 ADAPTERS (real + sim, one port each)  ───┼──┘
   SQLite / files         │  L3 INFRA (config, http, db, logging, hdrs)  │
                          └──────────────────────────────────────────────┘
                          ┌──────────────────────────────────────────────┐
                          │  COMPOSITION ROOT (the only impure wiring):   │
                          │  reads Settings → builds adapters by mode →   │
                          │  assembles services → immutable Container →   │
                          │  owns FastAPI lifespan (startup/shutdown)     │
                          └──────────────────────────────────────────────┘
```

**Dependency-inversion rule:** services depend on **ports**, never on adapters. Adapters are injected by the composition root. This makes every service unit-testable with sim/fake ports and lets real↔sim swap purely by config.

## 2. Layering & invariants (statically enforced)
| Layer | Dir | May import | Notes |
|---|---|---|---|
| L1 Domain | `domain/` | nothing | pure; no I/O, no vendor libs |
| L2 Ports | `ports/` | domain | abstract `Protocol`/ABC + request/result DTOs |
| L3 Adapters | `adapters/` | domain, ports, infra | the **only** place I/O / vendor SDKs live |
| L3 Infra | `infra/` | domain | config, http client, db, logging, headers |
| L4 Services | `services/` | domain, ports | use-cases; injected `Clock`/`IdGen` for determinism |
| L5 Controllers | `controllers/` | services (via DI), schemas | HTTP/WS only; construct nothing |
| Composition | `composition/`, `app.py` | everything | the one impure graph |

Two hard invariants checked by `tests/arch/test_layering.py` (static AST scan): **(a)** only `infra/config.py` reads `os.environ`; **(b)** only real adapters + `infra/http_client.py` + the PAVO adapters import a vendor SDK / `torch` / `httpx`. Regressions fail CI.

## 3. Ports & adapters (21 ports)

| Port | Sponsor / concern | Key methods | Real adapter | Sim adapter |
|---|---|---|---|---|
| `RoutingPort` | PAVO | `route`, `explain`, `is_feasible`, `tiers` | `torch_routing_adapter` | `numpy_routing_adapter` (bit-faithful) |
| `RetrievalPort` | Moss | `retrieve`, `health` | `moss_http_adapter` | `tfidf_retrieval_adapter` |
| `LLMGatewayPort` | TrueFoundry | `complete`, `stream`, `health`, `cost_to_date` | `tf_gateway_adapter` | `sim_gateway_adapter` |
| `RedactionPort` | TrueFoundry (PHI) | `redact_text`, `redact_messages`, `contains_phi` | `tf_redaction_adapter` | `local_redaction_adapter` |
| `AuditLogPort` | TrueFoundry (audit) | `append`, `verify_chain`, `iter` | `hashchain_audit_adapter` | (same, SQLite/memory) |
| `CostLedgerPort` | TrueFoundry (cost) | `record`, `snapshot`, `record_chars` | `cost_ledger_adapter` | (same) |
| `DenialParserPort` | Unsiloed | `parse`, `supports` | `unsiloed_http_adapter` | `deterministic_parser_adapter` |
| `ReasoningPort` | MiniMax | `compose_line`, `interpret_denial`, `health` | `minimax_adapter` | `local_reasoning_adapter` |
| `SpeechSynthesisPort` | Qwen | `synth`, `synth_stream`, `health` | `qwen_tts_adapter` | `sim_tts_adapter` |
| `VoiceTransportPort` | LiveKit | `open_channel`, `mint_join_token`, `bridge_nurse`, `close_channel` | `livekit_adapter` | `inprocess_transport_adapter` |
| `ConcurrencyGatePort` | AWS | `acquire`, `release`, `slot`, `ensure_capacity`, `capacity` | `fargate_gate` | `semaphore_gate` |
| `IvrPort` | in-house (sim-only) | `dial`, `navigate`, `hangup` | — | `ivr_sim_adapter` |
| `AppealRepositoryPort` | persistence | `save`, `load`, `list`, `update_status_atomic`, `append_event` | `sqlite_appeal_repo` | `memory_appeal_repo` (LRU+TTL) |
| `FileStorePort` | storage | `put`, `get_signed_url`, `open`, `delete`, `sweep_expired` | `s3_filestore_adapter` | `local_filestore_adapter` |
| `EventBusPort` | WS egress | `publish` (RedactedText only), `subscribe`, `close` | `ws_event_bus_adapter` | (same) |
| `LetterRenderPort` | PDF | `render` (RedactedAppealLetter → bytes) | `reportlab_letter_adapter` | (same) |
| `SignaturePort` | sign-off | `sign`, `verify` (Ed25519) | `ed25519_signature_adapter` | (same) |
| `AuthPort` | security | `authenticate`, `authorize` (RBAC) | `jwt_auth_adapter` | (same) |
| `ClockPort` / `IdGenPort` / `TaskSupervisorPort` | determinism / lifecycle | `now`/`monotonic`, `new_id`, `spawn`/`drain` | system adapters | fakes in tests |

## 4. Module / file map (97 files — one responsibility each)

```
orchestrator/backstop/
  domain/        models.py enums.py redacted.py money.py errors.py
                 appeal_aggregate.py(state machine) triage.py routing_policy.py carc_table.py
  ports/         routing_port.py retrieval_port.py llm_gateway_port.py redaction_port.py
                 audit_log_port.py cost_ledger_port.py denial_parser_port.py reasoning_port.py
                 speech_synthesis_port.py voice_transport_port.py concurrency_gate_port.py ivr_port.py
                 appeal_repository_port.py file_store_port.py event_bus_port.py letter_render_port.py
                 signature_port.py auth_port.py clock_port.py id_gen_port.py task_supervisor_port.py
  adapters/      pavo/{_coupling,torch_routing_adapter,numpy_routing_adapter}.py
                 moss/{moss_http_adapter,tfidf_retrieval_adapter}.py  text/runbook_corpus.py
                 truefoundry/{tf_gateway_adapter,sim_gateway_adapter,local_redaction_adapter,
                              tf_redaction_adapter,hashchain_audit_adapter,cost_ledger_adapter}.py
                 unsiloed/{unsiloed_http_adapter,deterministic_parser_adapter}.py
                 minimax/{minimax_adapter,local_reasoning_adapter}.py
                 qwen/{qwen_tts_adapter,sim_tts_adapter}.py
                 livekit/{livekit_adapter,inprocess_transport_adapter}.py
                 aws/{fargate_gate,semaphore_gate}.py  ivr/ivr_sim_adapter.py
                 persistence/{sqlite_appeal_repo,memory_appeal_repo}.py
                 filestore/{local_filestore_adapter,s3_filestore_adapter}.py
                 eventbus/ws_event_bus_adapter.py  letter/reportlab_letter_adapter.py
                 signoff/ed25519_signature_adapter.py  auth/jwt_auth_adapter.py
                 system/{system_clock_adapter,uuid_id_gen_adapter,asyncio_task_supervisor}.py
  services/      call_service.py swarm_service.py reconcile_service.py
                 ingest_denial_service.py ingestion_batch_service.py triage_service.py
                 appeal_service.py letter_service.py review_service.py signoff_service.py
                 nurse_bridge_service.py auth_service.py
  controllers/   dependencies.py schemas.py appeals_controller.py ingestion_controller.py
                 review_controller.py triage_controller.py files_controller.py ws_controller.py
  infra/         config.py http_client.py db.py logging.py security_headers.py
  composition/   adapter_factory.py container.py wiring.py
  app.py
  web/           (redesigned dashboard; per-component output encoding)
data/            runbooks/*.md  carc_rarc.json
tests/           arch/ unit/ contract/ services/ composition/ controllers/ security/ e2e/ load/
```

## 5. Composition root / DI
`composition/adapter_factory.py` holds `make_<port>(settings, shared)` factories that pick real-vs-sim per port from `Settings`. `wiring.py::build_container(settings)` constructs shared singletons (one `RedactionPort`, `AuditLogPort`, `CostLedgerPort`, `httpx.AsyncClient`, db engine, PAVO router), injects them into the gateway and every service, and returns an immutable `Container`. `app.py` builds the container once and owns the FastAPI **lifespan** (startup: `gate.reconcile()`, db migrate; shutdown: `task_supervisor.drain()`, close client/db). The **PAVO factory tries torch, falls back to numpy** — a single router singleton.

## 6. Request data-flow (POST /appeals → FILED)
```
Controller (authz) → AppealService.create(Denial)
  → ConcurrencyGatePort.slot(appeal_id):                      # AWS backpressure
      SwarmService fan-out per specialist (concurrent):
        CallService.handle_turn(turn):
          1. RoutingPort.route(obs)            → tier          # PAVO (sync, pure, ONE call/turn)
          2. if denial turn: RetrievalPort.retrieve(query)     # Moss (redacted query)
          3. ReasoningPort.compose_line(...) VIA LLMGatewayPort # MiniMax through TrueFoundry:
                redact-out → upstream → redact-in → audit append → cost record
          4. SpeechSynthesisPort.synth(RedactedText)           # Qwen (valid WAV)
          5. VoiceTransportPort frame → EventBusPort.publish   # LiveKit + WS (redacted-only)
  → ReconcileService.find_contradiction(...)                   # cross-desk
  → LetterService: compose → RedactionPort.redact → LetterRenderPort.render → FileStorePort.put
  → ReviewService enqueue → (nurse) → SignoffService:
        AuditLogPort.verify_chain()==True  AND  SignaturePort.sign(hash, nurse)
        → AppealRepositoryPort.update_status_atomic(AWAITING_SIGNOFF → FILED)   # CAS
```
ONDEVICE_FAST turns never call `ReasoningPort` (cost collapse). The repo lock is held only across the row/dict mutation, never across an `await` to a vendor → the swarm is genuinely parallel (fixes lock-scope + threadpool-starvation).

## 7. Per-sponsor integration detail
- **PAVO** — no key, no network, no PHI. Wraps `pavo_bench` inference only; weights frozen; numpy path is bit-faithful and torch-free. Env: `PAVO_ADAPTER_IMPL` (torch|numpy), optional `PAVO_WEIGHTS_PATH`.
- **Moss** — `POST {MOSS_BASE_URL}/v1/query`, project-scoped auth (`Authorization: Bearer MOSS_PROJECT_KEY` + `X-Moss-Project: MOSS_PROJECT_ID`). One bounded retry on 5xx. Query carries denial context (CARC/RARC, payer, CPT) — **never** member IDs. *Auth header shape is Open Q1.*
- **TrueFoundry** — OpenAI-compatible gateway; base URL + inference path env-configurable (tenant `hackathon12`). The sole LLM chokepoint: redacts both directions, hash-chains audit, prices cost. *Base URL/path is Open Q2.*
- **Unsiloed** — async create-extract-job + poll; bytes-only (no server path). Raw EDI routed to the deterministic sim parser. *Status casing / EDI accept is Open Q3.*
- **MiniMax** — native `chatcompletion_v2` (or OpenAI route); `MINIMAX_API_KEY` + `MINIMAX_GROUP_ID`. Receives only de-identified text. *Default model + GroupId placement is Open Q4.*
- **Qwen/DashScope** — TTS to a valid WAV; text must be `RedactedText` (no BAA → redacted side only).
- **LiveKit** — real HS256 access-token mint (room/identity/grant claims, TTL); room lifecycle is idempotent (fixes transport leaks); nurse barge-in = short-TTL join token.
- **AWS** — Fargate warm-pool admission (RunTask/StopTask) in real mode; `asyncio.Semaphore` in sim; both honor the same blocking-acquire contract.

## 8. Persistence
SQLite (WAL) behind `AppealRepositoryPort` / `AuditLogPort` / `CostLedgerPort`, replacing the unbounded global dict. Sim/test uses bounded **LRU+TTL** in-memory adapters honoring the same contract (atomic CAS, append-only). Money as **integer cents/micros** (no float). PHI columns are **salted hashes only**.

```
appeals(id PK, status[DENIED|TRIAGED|IN_APPEAL|AWAITING_SIGNOFF|FILED|ABANDONED], payer_id,
        member_id_hash, claim_number_hash, denial_json[redacted], recoverable_cents,
        sol_deadline, route_decision, needs_human_review, created_at, updated_at,
        version[optimistic lock])  INDEX(status, sol_deadline, needs_human_review)
events(id PK, appeal_id FK, seq, ts, kind, payload_json[RedactedText only], prev/record hashes)  -- append-only
audit / cost_ledger / signoffs   -- append-only, hash-chained where applicable
```
`DATABASE_URL` allows a Postgres swap later without touching services.

## 9. Security architecture (maps the 24 findings)
- **Auth (Critical ×2):** `AuthPort`/`jwt_auth_adapter` enforces authn on **every** HTTP route + the WS handshake via a `get_principal` dependency; RBAC `authorize(principal, action, resource)` gates per-appeal ownership for `GET /appeals/{id}`, `/files`, and the nurse queue. A parametrized test enumerates the route table so any new unguarded route fails CI.
- **Structural PHI boundary (Critical ×3 + High):** `RedactionPort` is the **sole** producer of `RedactedText`; every egress port accepts only `RedactedText` for PHI fields → unredacted PHI is a **type error**. Runtime defense-in-depth: each egress adapter calls `contains_phi()` and refuses on a hit. The gateway redacts outbound prompt **and** inbound completion. Redaction is hypothesis-fuzzed for member/NPI/SSN/DOB/claim/name patterns.
- **BAA-gated routing:** MiniMax, Qwen, Moss sit strictly on the redacted side; PAVO + the gate never receive PHI (telemetry + surrogate `appeal_id` only).
- **Tamper-evident audit:** SHA-256 hash chain; `verify_chain()` detects any field flip and is **required** before sign-off + the FILED transition.
- **Path traversal (Critical):** two layers — (1) the parser/ingestion controller accept only in-memory validated bytes + a declared `ArtifactKind` (no server-side path opened from user input); (2) `LocalFileStore` path-jail + signed-token access.
- **Resource/DoS (High):** upload size caps + content-type validation; per-appeal concurrency slot; bounded repo (LRU+TTL / SQLite); `FileStore.sweep_expired` TTL cleaner (fixes the 212-PDF leak); tracked tasks + graceful drain.
- **Web (High/Med):** CORS allowlist + CSP + security headers (`security_headers.py`); WS origin check + token handshake; per-component output encoding in the dashboard.

## 10. Concurrency model
Async-first hot path. `SwarmService` fans out `CallService` runs, each wrapped in `async with gate.slot(appeal_id)`. The `AppealRepository` lock is held only across in-memory/row mutation, **never across an await** to a vendor, so routing/retrieval/reasoning happen lock-free. `update_status_atomic` is a compare-and-set on `version` (fixes the snapshot-watchdog race). `TaskSupervisorPort` tracks every spawned task and drains/cancels on shutdown (fixes fire-and-forget + no-graceful-shutdown).

## 11. Config, logging, observability, errors
- **Config:** `infra/config.py` is the only `os.environ` reader — a frozen `pydantic-settings` `Settings` (all keys, base URLs, modes, limits, prices).
- **Logging:** `infra/logging.py` structured JSON logs with a PHI-scrubbing filter.
- **Observability:** live cost ledger (PAVO vs frontier), capacity meter, real/sim health per port, `/healthz` (liveness) vs `/readyz` (readiness).
- **Errors:** a domain exception hierarchy; adapters translate vendor errors into domain errors (services never see `httpx`).

## 12. Deployment
Multi-stage Dockerfile (torch optional layer); `docker-compose` for local; GitHub Actions CI lanes — **ruff → mypy --strict → pytest (unit/contract/services/security) → load smoke → bandit + pip-audit**. Pinned dev stack: pytest 8, pytest-asyncio (auto), pytest-cov (branch), pytest-httpx, hypothesis, freezegun, ruff, mypy.

## 13. Open questions (please weigh in before/with approval)
1. **Moss** live auth header shape (`Authorization: Bearer` + `X-Moss-Project` vs project-id-in-path) and whether an official PyPI SDK exists — adapter isolates this; default is the Bearer+header form.
2. **TrueFoundry** inference host+path for tenant `hackathon12` (`llm-gateway.truefoundry.com` vs `<tenant>.truefoundry.cloud`, `/api/inference/openai` vs `/api/llm/...`) — made `BASE_URL` + `INFERENCE_PATH` env-configurable + validated by a `health()` probe.
3. **Unsiloed** status-string casing / 200-vs-201 / large-EDI accept — UNCERTAIN; design routes **all raw EDI to the deterministic sim parser** (Unsiloed used for image/PDF EOBs).
4. **MiniMax** default model id + whether `GroupId` is a header or query param. **Which model tier do you want as default** for `compose_line` (speed) vs `interpret_denial` (reasoning)?
5. **Database:** SQLite is my default for a single-node hackathon RCM tool. **Do you anticipate multi-node scaling in this build** (→ Postgres now)? The port makes the swap free either way.
6. **Runbook corpus:** `data/runbooks/` does not exist yet and is load-bearing for Moss-sim retrieval + MiniMax-sim reasoning + seeding the real Moss index. **How many runbooks / which payers + denial codes** beyond the contract-test minimum (CO-197, CO-50/CO-45, N130)?
7. **Sim voice realism:** the Qwen sim emits a length-correct formant-tone WAV (not speech). OK for the demo, or add an optional offline real-TTS (espeak/pyttsx3) sim variant?
8. **PAVO weights export:** commit a `meta_controller_weights.npz` so the numpy adapter + CI run with **zero torch**, or keep loading the `.pt` (needs torch present)?
