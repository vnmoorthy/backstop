# Backstop — 90-second demo runbook

**Setup (before you present):**
```bash
cd orchestrator && python -m backstop.server
# open http://localhost:8000/ — wait for the conn pill to read "live"
```
The dashboard has a built-in **replay fallback**: if the socket is down, the Run button plays a canned sequence, so the demo survives a dead network. Keep a backed-up screen recording too.

---

### The 90 seconds

**[0:00–0:12] Frame the money (one breath).**
> "Providers write off **$262 billion** a year in denied claims. Two-thirds are never appealed — not because they'd lose, but because no human will sit on hold 25 minutes for a $30 line item. We work that phone appeal. And we work it on the one line where automated calling is clean: provider-to-payer, where we're the hospital's BAA'd agent."

**[0:12–0:25] Detonate.** Click **Run sample denial**. The Unsiloed spec card fills (Aetna **CO-197**, claim CLM-55-7741, $2,480). The **PAVO singularity bursts** into three specialist cells — provider line, prior-auth desk, records desk — all dialing at once.
> "One denial just became a swarm of three calls, in parallel."

**[0:25–0:50] The cost collapse (the hero).** Watch the **particle stream**: a flood of **mint** particles (IVR nav, hold) with **magenta flares** firing exactly on the turns where the rep states the denial reason. The **cost ticker** climbs to **PAVO $0.024 vs frontier $0.105 → 4.4x cheaper**.
> "Ninety percent of the call is hold music — PAVO keeps that on a near-free local model and spends a frontier model only on the two turns that matter. At frontier cost this appeal *loses* money. That's why no one calls. My routing research is the only reason the math works."

**[0:50–1:05] Moss wins the call.** As each rep states the denial reason, a **Moss rebuttal card** flashes on the cell: *"Run the auth lookup by the RENDERING NPI, not the billing NPI — 73% win rate."*
> "The instant the rep says 'no auth on file,' Moss retrieves the exact rebuttal that overturns it — mid-call."

**[1:05–1:20] The contradiction → the letter.** The **reconciler** card lights up: provider line says *"no prior authorization on file"* (red) — contradicted by the records desk: *"authorization A4471 was issued"* (green). An **appeal letter PDF** drops, quoting both reps verbatim.
> "Three desks, one contradiction. The denial is overturned — and here's the verbatim, audit-grade letter."

**[1:20–1:30] Land it.** Click **Nurse: sign & file**.
> "A licensed nurse signs — we never file autonomously. That denial just got overturned for **three cents of compute**. Same ad spend, same backlog, 24/7. We own the phone, and a Claude Skill has no hands."

---

### If a judge pushes
- **"Is it real?"** → Point at the sponsor row: every badge shows `real`/`sim`. PAVO is real (TMLR weights). The cost ratio is real token/tier math. We never dial real payers and use zero PHI — by policy, shown in the footer.
- **"Routing is a feature."** → It's the *coupling-constraint* research (TMLR), not a price flag — `tests/test_router.py` shows the masked escalation and the constant-without-mask failure mode.
- **"TCPA / consent."** → Provider-to-payer B2B lines only; BAA'd agent; AI disclosed; nurse signs.
