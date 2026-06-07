# BACKSTOP — the pitch

> The voice-agent swarm that recovers denied health-insurance claims by winning the phone appeal.
> Powered by the founder's PAVO router (TMLR 2026).

---

## The hook

A denial detonates into a swarm of AI agents that call the payer's provider line, prior-auth desk, and records desk **in parallel**, sit the holds, retrieve the exact winning rebuttal the instant the rep states the denial reason, find the contradiction that overturns the denial, and hand a licensed appeals nurse a verbatim, audit-grade letter to sign and file. We automate the one thing nobody automates: **the phone appeal.**

`PAVO $0.024  ·  frontier $0.105  ·  4.4x cheaper per appeal` *(verified, this repo)*

## The problem — $262B on the floor

US providers write off **~$262B/yr** in denied claims. **~65% are never appealed** — not because they'd lose, but because no human will sit on hold 25 minutes to recover a $30 line item. The recovery work is *phone* work, and at frontier-model cost a per-appeal call **loses money**. That's the gap: the dollars are recoverable, the labor isn't worth it, and naive automation is too expensive to flip the math.

## The insight — PAVO makes the phone appeal economical

A single appeal is 6–12 calls, each 30–60 turns, and almost all of those turns are IVR navigation and hold music. **PAVO routes ~90% of call turns to a near-free local tier and spends a frontier model only on the 2–3 turns where the rep states the denial reason.**

The routing intelligence is the **coupling mask** (the paper's contribution), not a price flag: PAVO's released policy is constant — for a turn of complexity *C*, profiles that can't serve *C* are masked out, so the choice escalates as the turn gets harder. The mask is load-bearing and test-covered.

| measure | result |
|---|---|
| provider call | **5.4x** cheaper |
| full appeal | **4.4x** cheaper (PAVO $0.024 vs frontier $0.105) |
| router | masked PAVO, **85,041 params**, vendored TMLR weights |

That collapse is the difference between a $30 appeal that loses money and one that makes it. **It is the only reason the business exists.**

## The product — the swarm

Upload a denial. Unsiloed parses the EOB into a structured appeal spec. A concierge fans it out, by denial-code gates, into specialist call-agents that dial concurrently. Each call runs a per-turn loop — transport, signal extraction, **PAVO route**, **Moss retrieve**, compose, TTS, cost-log — over LiveKit, through the TrueFoundry gateway. A reconciler diffs the transcripts and finds the overturning contradiction. A letter-writer drafts the verbatim appeal PDF. **A nurse signs — the swarm never files autonomously.**

## The demo's wow

One click. The Unsiloed spec card fills (Aetna **CO-197**, $2,480). The PAVO singularity bursts into three calling cells. A flood of **mint** particles (hold music) streams by with **magenta flares** firing exactly on the denial-reason turns, while the cost ticker climbs to **4.4x cheaper, live**. A Moss card flashes — *"run the auth lookup by the RENDERING NPI, not the billing NPI — 73% win rate"* — the reconciler lights up a red/green contradiction, and an audit-grade letter PDF drops. **A denial overturned for three cents of compute, end-to-end in under 90 seconds.**

Stress-tested: **100 concurrent appeals (~300 agents), 100/100 succeed, avg 4.40x, 2.4s, 0 errors.**

## Market + model

Clients are mid-market **hospital billing departments + outsourced RCM / medical-billing companies**, paid on **contingency: 25–30% of recovered dollars** — no integration risk, no upfront spend, pure found money. First design partner: a regional system with a **3,000-denial written-off backlog**. The TAM is the $262B itself, and the ~65% never-appealed slice is greenfield by definition.

## The moat — Death-by-Clawd

The reflexive challenge is "isn't this just a prompt?" **A prompt has no hands, no phone line, no BAA.** It cannot place a provider-to-payer call as a covered entity's agent. The moat is two compounding assets a wrapper can't copy:

1. **The coupling-constraint research** (PAVO / TMLR) that makes the phone-appeal economics close at all — the 4.4x is the wall.
2. **The compounding payer-rebuttal corpus** — every call we win makes the next call across that payer/denial-code cheaper to win. Moss retrieval over that corpus is a flywheel, not a feature.

## All 7 sponsors, load-bearing

| Sponsor | Role | Badge |
|---|---|---|
| **PAVO** *(founder IP)* | per-turn masked routing — the cost collapse | real |
| **Moss** | real-time rebuttal + precedent retrieval, fired mid-call | real / sim |
| **LiveKit** | voice transport for the concurrent swarm + nurse bridge | real / sim |
| **TrueFoundry** | gateway for every model call: PHI redaction + audit log + cost ledger | real / sim |
| **Unsiloed** | parses the denial EOB into the appeal spec | real / sim |
| **AWS** | elastic burst to hundreds of call containers, then scale to zero | real / sim |
| **MiniMax** | mid-tier reasoning + multilingual completion | real / sim |
| **Qwen** | one "appeals coordinator" brand voice, multilingual TTS | real / sim |

Every integration carries a live `real | sim` badge — `real` when its key is set, a deterministic local sim otherwise. **Nothing is misrepresented as real.**

## Founder-market fit

I authored **PAVO** (pipeline-aware demand-conditioned routing, **TMLR 2026**). The exact research that makes a phone appeal pencil is my own published contribution — vendored, masked, and test-covered in this repo. The person who built the wall is the person building the company on top of it.

## Compliance posture

Provider-to-payer **B2B lines only** — we're the covered entity's BAA'd agent, AI disclosed at call open, recording consent-clean. Synthetic data only, sandbox IVRs, **zero PHI**. The swarm never files; a licensed nurse signs.

---

## The ask

We're raising to convert the 3,000-denial design-partner backlog into recovered dollars and stand up the BAA'd live-calling path — **partner us with one RCM book of denied claims and we'll show you the recovery rate.**
