# Backstop — Parallel Build Plan (multi-session / multi-PR)

**Goal:** build the rebuild fast by fanning the milestones (`BUILD_PLAN.md` M0–M18) across multiple cloud sessions on separate branches → PRs → merge. The hexagonal architecture makes this **conflict-minimal**: ports are frozen contracts, and each adapter is an isolated file with its own contract test.

---

## The strategy: foundation-first → fan-out → integration-last

```
            ┌──────────────────────────────────────────────┐
  PR #1     │  WS0  FOUNDATION  (MUST MERGE FIRST)          │  serial prerequisite
            │  scaffold + config + domain + ALL 21 ports +  │
            │  composition skeletons + arch-guard test +    │
            │  shared data (carc_rarc.json, runbooks, corpus)│
            └───────────────────────┬──────────────────────┘
                                    │ once merged, contracts are frozen ↓
   ┌──────────┬──────────┬─────────┼─────────┬──────────┬──────────┬──────────┐
 PR#2       PR#3       PR#4       PR#5      PR#6       PR#7       PR#8       PR#9 …
 PAVO    TrueFoundry  Moss      Unsiloed  MiniMax     Qwen      LiveKit     AWS   ← all PARALLEL,
 (M2)    (M3-M5)    (M6-M7)*    (M8)      (M9)        (M10)     (M11)      (M12)    conflict-free
            │  + PR#10 Platform adapters (M13) + PR#11 Services (M14-M16)  │
            └───────────────────────┬──────────────────────────────────────┘
                                    │ after all adapters+services merged ↓
            ┌──────────────────────────────────────────────┐
  PR #12    │  WS-INTEGRATION  (M17): composition wiring +  │  serial, one owner
            │  controllers + middleware + lifespan          │  (resolves any conflicts)
            └───────────────────────┬──────────────────────┘
  PR #13    │  WS-HARDENING (M18): 24 security tests + e2e + load + Docker + CI │
            └──────────────────────────────────────────────┘
   *shared data (carc_rarc.json, runbooks/, runbook_corpus.py) lives in FOUNDATION,
    so Moss-sim and MiniMax-sim have no cross-dependency.
```

## Why this is conflict-free
Each adapter workstream **only adds files** under its own `adapters/<sponsor>/` folder plus one `tests/contract/test_*.py` file. **No two adapter branches touch the same file.** The only shared files —
`composition/adapter_factory.py`, `composition/wiring.py`, `infra/config.py`, `pyproject.toml` —
are created (with all keys + stubbed bindings) in **WS0 Foundation** and then edited **only** by **WS-Integration (PR #12)**. Adapter sessions never edit them.

## Rules of engagement (every session reads this)
1. **Branch off `main` only after WS0 is merged** (or rebase onto it). Branch name = the WS table below.
2. **Touch only your owned files.** Never edit `config.py`, `wiring.py`, `adapter_factory.py`, or `pyproject.toml` — list any new env keys/bindings you need in your PR description; the integration owner wires them.
3. Your PR **must** pass your milestone's **contract/unit test gate** (see `BUILD_PLAN.md`) + `ruff` + `mypy --strict`. CI enforces it.
4. **One responsibility per file.** Real adapter and sim adapter are separate files; both must satisfy the same port (the contract test runs both).
5. `git fetch && git rebase origin/main` before opening/refreshing the PR. Small, additive diffs → trivial merges.
6. Conventional-commit titles: `feat(adapter-moss): RetrievalPort real+sim (M6-M7)`.
7. Author email for commits: `182589719+vnmoorthy@users.noreply.github.com`.

## Workstream table
| PR | WS | Milestone(s) | Branch | Owns (files) | Depends on | Conflict risk |
|----|----|----|----|----|----|----|
| #1 | **Foundation** | M0–M1 + all ports + shared data | `feat/foundation` | `infra/*`, `domain/*`, `ports/*` (21), `composition/*` skeletons, `pyproject.toml`, `data/carc_rarc.json`, `data/runbooks/*`, `adapters/text/runbook_corpus.py`, `tests/arch`, `tests/unit/test_domain_*` | — | n/a (merge first) |
| #2 | PAVO | M2 | `feat/adapter-pavo` | `adapters/pavo/*`, `tests/contract/test_routing_port_contract.py`, weights `.npz` export | #1 | none |
| #3 | TrueFoundry | M3–M5 | `feat/adapter-truefoundry` | `adapters/truefoundry/*`, `tests/{unit,contract}/test_{redaction,audit_chain,cost_ledger,gateway}*` | #1 | none |
| #4 | Moss | M7 | `feat/adapter-moss` | `adapters/moss/*`, `tests/contract/test_retrieval_contract.py` | #1 | none |
| #5 | Unsiloed | M8 | `feat/adapter-unsiloed` | `adapters/unsiloed/*`, `tests/contract/test_parser_contract.py` | #1 | none |
| #6 | MiniMax | M9 | `feat/adapter-minimax` | `adapters/minimax/*`, `tests/contract/test_reasoning_contract.py` | #1 | none |
| #7 | Qwen | M10 | `feat/adapter-qwen` | `adapters/qwen/*`, `tests/contract/test_tts_contract.py` | #1 | none |
| #8 | LiveKit | M11 | `feat/adapter-livekit` | `adapters/livekit/*`, `tests/contract/test_transport_contract.py` | #1 | none |
| #9 | AWS | M12 | `feat/adapter-aws` | `adapters/aws/*`, `tests/contract/test_gate_contract.py` | #1 | none |
| #10 | Platform | M13 | `feat/adapter-platform` | `adapters/{persistence,filestore,eventbus,letter,signoff,auth,system,ivr}/*` + their contract tests | #1 | none |
| #11 | Services | M14–M16 | `feat/services` | `services/*`, `tests/services/*` (use fake ports as doubles) | #1 | none |
| #12 | **Integration** | M17 | `feat/integration` | `composition/{adapter_factory,wiring}.py`, `app.py`, `controllers/*`, `infra/{security_headers,logging}.py` | #2–#11 | the wiring point (1 owner) |
| #13 | Hardening | M18 | `feat/hardening` | `tests/{security,e2e,load}/*`, `Dockerfile`, `.github/workflows/ci.yml` | #12 | none |

**Merge order:** #1 → (#2…#11 in any order, parallel) → #12 → #13.

## Suggested session assignment (tune to how many sessions you have)
- **3 sessions:** S1 = Foundation→Integration→Hardening (the spine); S2 = TrueFoundry + Moss + Unsiloed + MiniMax; S3 = PAVO + Qwen + LiveKit + AWS + Platform + Services.
- **6 sessions:** S1 spine; S2 TrueFoundry; S3 Moss+Unsiloed; S4 MiniMax+Qwen; S5 LiveKit+AWS; S6 Platform+Services.

## Per-workstream brief (copy-paste into each cloud session, after #1 merges)
> **Repo:** github.com/vnmoorthy/backstop · base `main` (after PR #1 Foundation is merged).
> **Read first:** `docs/SYSTEM_DESIGN.md` (your port + adapters), `docs/BUILD_PLAN.md` (your milestone), this file's **Rules of engagement**.
> **Task:** `git checkout -b <branch>`. Implement ONLY the files your WS owns (real adapter + sim adapter + contract test). Both adapters must satisfy the port `Protocol` already on `main`. Do **not** edit `config.py`/`wiring.py`/`adapter_factory.py`/`pyproject.toml` — list new env keys in the PR body.
> **Gate:** `pytest <your contract test> -q` GREEN + `ruff check` + `mypy --strict <your package>` clean. Then `gh pr create` with a conventional title and a checklist mapping to your milestone's GATE.

## Status (live)
| PR | Branch | State |
|----|----|----|
| #1 Foundation | `feat/foundation` | _building now (this session)_ |
| #2–#13 | — | _open after #1 merges; dispatch briefs above_ |
