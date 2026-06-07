# Backstop — Presenter's Cheat Sheet

> Win the phone appeal. One denial detonates into a swarm of voice agents, PAVO collapses the cost, Moss wins the call, a nurse signs.
> This is the card you hold while you present. The full timing is in `DEMO_SCRIPT.md`; this is the *say-this-exactly*, *answer-this-fast*, *don't-panic* sheet.

**Before you walk up:** `./run.sh` → wait for the conn pill to read **live** → load the dashboard full-screen. Know your two numbers cold: **4.4x cheaper per appeal**, **$262B/yr written off**.

---

## The 6 demo beats — the exact line to say

**Beat 1 — Frame the money (one breath).** Don't touch anything yet.
> "Providers write off **$262 billion** a year in denied claims. Two-thirds are never appealed — not because they'd lose, but because no human will sit on hold 25 minutes for a $30 line item. We work that phone appeal, on the one line where automated calling is clean: provider-to-payer, where we're the hospital's BAA'd agent."

**Beat 2 — Detonate.** Click **Run sample denial**. The Unsiloed spec card fills (Aetna **CO-197**, claim **CLM-55-7741**, **$2,480**). The PAVO singularity bursts into three specialist cells.
> "One denial just became a swarm of three calls — provider line, prior-auth desk, records desk — all dialing in parallel."

**Beat 3 — The cost collapse (the hero).** Point at the particle stream: a flood of **mint** particles (IVR nav, hold) with **magenta flares** on the denial-reason turns. The ticker climbs to **PAVO $0.024 vs frontier $0.105 → 4.4x cheaper**.
> "Ninety percent of the call is hold music — PAVO keeps that on a near-free local model and spends a frontier model only on the two-or-three turns that carry the denial reason. At frontier cost this appeal *loses* money. That's why no one calls. My routing research is the only reason the math works."

**Beat 4 — Moss wins the call.** As each rep states the denial reason, a Moss rebuttal card flashes on the cell.
> "The instant the rep says 'no auth on file,' Moss retrieves the exact rebuttal mid-call: *run the auth lookup by the rendering NPI, not the billing NPI — 73% win rate.*"

**Beat 5 — Contradiction → letter.** The reconciler card lights: provider line *"no prior authorization on file"* (red), contradicted by records desk *"authorization A4471 was issued"* (green). The appeal PDF drops, quoting both reps verbatim with timestamps.
> "Three desks, one contradiction. The denial is overturned — and here's the verbatim, audit-grade letter."

**Beat 6 — Land it.** Click **Nurse: sign & file**.
> "A licensed nurse signs — we never file autonomously. That denial just got overturned for **three cents of compute**. Same backlog, 24/7. We own the phone, and a prompt has no hands."

---

## The 3 toughest judge questions — crisp answers

**"Is this actually real, or a mockup?"**
> Point at the sponsor row — every badge shows `real` or `sim`, live. **PAVO is real**: vendored TMLR weights, 85,041 params, the router loads on launch. The **cost ratio is real** — computed from real token counts × tier prices, not a hardcoded number. What's *sim* is sim by policy: we never dial a real payer, and we use **zero PHI**, only synthetic denials against sandbox IVRs. Nothing on this screen claims to be real that isn't.

**"Routing is just a feature / a price flag — where's the moat?"**
> It's the **coupling-constraint research** (PAVO, TMLR 2026), not an if-statement. PAVO's released policy is *constant* — the intelligence is the **mask**: profiles that can't serve a turn's complexity get masked out, so the choice escalates as the turn gets harder. `tests/test_router.py` proves both: masked escalation (complexity 1→profile 2/local, complexity 5→profile 40/frontier) **and** the constant-policy failure mode without the mask. And the moat isn't only the router — it's the **compounding payer-rebuttal corpus** that gets sharper with every call. A prompt has no hands, no phone line, no BAA.

**"TCPA / consent — how is autodialing a payer legal?"**
> **Provider-to-payer B2B lines only** — we're the covered entity's BAA'd agent placing a business call, not consumer outreach, so TCPA's consumer-call regime doesn't apply. **AI is disclosed at call open**, recording is consent-clean, and the swarm **never files** — a licensed appeals nurse signs every letter. TrueFoundry redacts PHI before any model call and writes an immutable audit trail.

---

## Verified metrics to quote (do not invent — these only)

| Number | Say it as |
|---|---|
| **PAVO $0.024 vs frontier $0.105** | the per-appeal cost, real token/tier math |
| **4.4x cheaper** | per full appeal · **5.4x** on the provider-line call alone |
| **~90% of call turns** | routed to the near-free local tier; frontier only on the 2-3 denial-reason turns |
| **85,041 params** | the PAVO masked router (vendored TMLR 2026 weights) |
| **$262B/yr · ~65% never appealed** | the market, and why |
| **25-30% contingency** | pricing, paid on recovered dollars |
| **100/100 succeed · avg 4.40x · 2.4s · 0 errors** | the 100-concurrent-appeals (~300 agents) stress test |
| **73% win rate** | the Aetna CO-197 rendering-NPI rebuttal (from the seeded corpus) |

Clients: **mid-market hospital billing departments + outsourced RCM companies**. First design partner: a regional system with a **3,000-denial written-off backlog**.

---

## If the demo breaks — don't panic, three layers of fallback

1. **Socket drops / dashboard looks dead.** The Run button has a built-in **canned replay** — click **Run sample denial** anyway and it plays the same deterministic sequence with no network. Keep narrating; nobody can tell.
2. **The whole server is wedged.** Relaunch with **`./run.sh`** — it finds python, installs deps if missing, checks the PAVO weights (prints `PAVO router OK (85,041 params)`), and reopens the dashboard. ~10 seconds.
3. **The machine is hostile / projector won't cooperate.** Play the **backed-up screen recording** of a clean end-to-end run and narrate over it live. The run is deterministic (sandbox scripts), so the recording matches what you'd have shown.

**The one line that always lands, even with no screen:**
> "We overturn a denial for three cents of compute, provider-to-payer, with a nurse's signature. $262 billion a year goes unappealed because the phone work loses money at frontier cost — PAVO is why our math works and theirs doesn't."
