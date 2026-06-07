# Backstop — 2-Minute Demo Video Script

**Cast:** Pranav · Moorthy (founder / PAVO author) · Sreekanth
**Record on:** localhost:8000 (real badges + real MiniMax). Pick **Aetna CO-197** before recording.
**Total:** 2:00. Practiced once, this lands.

---

### [0:00–0:18] — PRANAV (the problem)
> "Every year, U.S. hospitals write off **billions** in denied insurance claims — not because the denials are valid, but because appealing them is too slow and too expensive to be worth a nurse's time. The money just… disappears. **We built the swarm that gets it back.**"

### [0:18–0:42] — MOORTHY (what it is + the IP) — *click "Work this denial"*
> "This is **Backstop** — a swarm of 23 AI agents that recovers denied claims by winning the phone appeal. Watch: one denial detonates into specialist agents that call the payer's provider line, billing, and records desks **in parallel**. And every turn is routed by **PAVO** — my masked router, published at TMLR — which collapses the model cost **four to six times**. *That's* what makes this economically possible."

### [0:42–1:18] — SREEKANTH (narrate the live run)
> "The agents navigate the IVR, survive the hold, and **Moss retrieves the winning rebuttal the instant the rep states the denial reason.** The reconciler finds the contradiction that overturns it — here, the prior auth was on file the whole time. And this appeal argument? **Generated live by MiniMax**, routed through TrueFoundry, fully PHI-redacted and audited."
> *(letter appears)* "It drafts a verbatim, audit-grade appeal letter for the nurse." *(click "Nurse — sign & file")* "She signs once — and the **entire 23-agent workforce deploys** to verify coverage, file the appeal, and track the recovery."

### [1:18–1:44] — MOORTHY (the money / business model)
> "And here's why hospitals say yes: we ran a real remittance file — **$8,430 written off became $7,240 recovered**, and our invoice was **$1,954.80**. We bill **25–30% of recovered dollars. Zero risk, pure upside.** Scale that to a 3,000-claim backlog and it's a seven-figure recovery they'd already given up on."

### [1:44–2:00] — PRANAV (the moat + close)
> "Waystar surfaces denials. Experian predicts them. **Nobody autonomously recovers them on the phone, at a cost that makes contingency work** — because that needs a real voice swarm, a compounding win-corpus, and PAVO. **Backstop. We don't just find the money — we win it back.**"

---

## Stage directions cheat-sheet
| Time | Who | Action |
|---|---|---|
| 0:18 | Moorthy | click **Work this denial** |
| 0:42 | — | swarm cells + cost ticker climbing on screen |
| 1:05 | Sreekanth | letter card appears → click **Nurse — sign & file** |
| 1:08 | — | 23-agent deploy cascade fills the screen |
| 1:18 | Moorthy | (optional) cut to terminal: `python3 -m backstop.rcm.backlog_demo` showing the invoice |

## Tips
- Hard-refresh (Cmd+Shift+R) once before recording.
- Keep energy up; the numbers ($7,240 → $1,954.80) and "23 agents" + "4–6× cheaper" are the lines that stick.
- If asked live: be upfront that phone calls run under BAA on real backlogs — the demo uses sandbox IVRs + synthetic PHI. Honesty impresses healthcare judges.
