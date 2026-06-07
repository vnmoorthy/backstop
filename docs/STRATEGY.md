# Backstop — Strategy, Competitors & Standout

**For:** vnmoorthy · **Purpose:** what we sell, who we beat, how we stand out, and the demo-video script.

---

## 1. What we do for clients (the value, in one line)
**We recover the denied claims a hospital already wrote off — and we only get paid when they get paid.**

The client (a hospital billing department or an outsourced RCM firm) hands us a backlog of denials. A swarm of AI agents works each one — verifies coverage, finds the authorization, calls the payer, finds the contradiction that overturns the denial, drafts the nurse-signed appeal, files it, and tracks the recovery. We bill **25–30% of recovered dollars** — pure upside for them, zero risk.

**The ROI, concretely (from our own engine, real X12 835):**
> a $8,430 written-off batch → **$7,240 recovered** (60% win) → our invoice **$1,954.80**. Scale that to a 3,000-claim backlog and it's a seven-figure recovery the client had already given up on.

## 2. The competitive landscape (who's out there)
| Company | What they offer | Where they're weak vs us |
|---|---|---|
| **Waystar / Availity / Optum (Change)** | Clearinghouses + denial dashboards + claim-status AI | Workflow/visibility tools, not *autonomous recovery*; they surface denials, they don't win the appeal for you |
| **Experian Health (AI Advantage)** | Denial **prediction** + triage | Predicts denials; doesn't work the backlog or place calls |
| **Thoughtful AI** | Named RCM agents (eligibility, claims, posting) | Multi-agent RCM, but eligibility/posting focus — not denial-*appeal* recovery, no voice swarm, no cost-routing IP |
| **Adonis / Janus / Rivet / Anomaly** | Denial analytics + automation | Analytics-first; human still works the appeal |
| **Infinitus** | AI **voice** agent that calls payers (benefits/auth/status) | Single-purpose calls (verification), not concurrent appeal-winning swarms; no contingency model |
| **AKASA** | Generative AI for RCM ops | Ops-assist copilots, not an end-to-end contingency recovery product |

**The gap nobody fully owns:** *autonomous, contingency-billed recovery of the written-off denial backlog, won on the phone, at a routed cost that makes contingency economics work.* That's us.

## 3. How we stand out (the moat, honestly stated)
1. **PAVO cost-collapse IP** — our founder's TMLR-published masked router cuts per-task model cost ~4–6×. Contingency margins live or die on cost-to-recover; nobody else has this. *(Real, built.)*
2. **The 23-agent recovery workforce** (see §4) — not a dashboard, a digital team that works the claim end to end.
3. **Real-time concurrent voice appeals** — a swarm that calls the provider line + billing + records desks *in parallel* and wins the appeal. Infinitus does single calls; we do swarms aimed at recovery. *(Roadmap: needs telephony + BAA.)*
4. **The compounding win-corpus** — every recovered dollar teaches the system which argument beats which payer on which denial. A data network effect competitors can't copy. *(Roadmap.)*
5. **Contingency + closed-loop attribution** — we tie each recovered dollar to our action (audit-grade), which makes "pay us on recovery" defensible and the deal a no-brainer.

## 4. The agent workforce — 23 specialized agents (`backstop.agents.roster`)
A denials team isn't buying a tool; they're buying a **workforce**. Nine phases, every sponsor load-bearing, **18 of 23 backed by real code today**:

| Phase | Agents |
|---|---|
| **Intake** | Intake Agent (Unsiloed) · Document/OCR Agent |
| **Triage** | Triage Agent (PAVO) · Disposition Agent (MiniMax) |
| **Route/Prep** | PAVO Router · Eligibility · Prior-Auth (Moss) · Records (Moss) · Coding (MiniMax) · Supervisor (AWS) |
| **Call** | Provider-Line / Billing-Office / Records-Desk callers (LiveKit) · IVR Navigator · Voice Agent (Qwen) |
| **Reason** | Rebuttal Retrieval (Moss) · Reconciler |
| **Draft** | Letter Writer (MiniMax) · Compliance/PHI (TrueFoundry) |
| **File** | Filing Agent (portal/fax/clearinghouse) |
| **Recover** | Status Tracker (277/remit) · Recovery & Billing |
| **Learn** | Learning Agent (updates win-corpus + PAVO) |

## 5. Features to add to stand out (prioritized)
**Now (closes the billable loop):** Backlog & Recovery board · one-click filing (portal/fax) · 277/remit outcome polling · the recovered-$ + invoice ledger.
**Next (sells to hospitals):** denial-prediction (beat Experian at their own game) · payer-specific win playbooks · CFO ROI dashboard · nurse review queue + cryptographic sign-off · SSO/RBAC + BAA + tamper-evident audit.
**Later (the platform/moat):** the compounding win-corpus + per-payer PAVO finetuning · payer-behavior/IVR intelligence · Epic/Cerner write-back · multi-tenant white-label for RCM firms · denial root-cause prevention.

## 6. The 90-second demo-video narrative
1. **Hook (0:10):** "Hospitals write off billions in denied claims because appealing is too slow and too expensive. Backstop recovers them — and only gets paid when they do."
2. **The backlog (0:20):** drop in a real 835 remittance → it explodes into a prioritized worklist (recoverable $ × deadline).
3. **The swarm (0:35):** click one denial → 23 agents light up across the board; the voice swarm dials the payer in parallel, navigates the IVR, retrieves the winning rebuttal, finds the contradiction.
4. **The cost collapse (0:50):** the PAVO ticker shows ~5× cheaper than all-frontier — "this is why contingency works."
5. **The win (1:05):** the nurse-signed appeal letter; the follow-up remit flips the claim to PAID.
6. **The money shot (1:20):** "$8,430 written off → $7,240 recovered → here's our invoice for $1,954.80. Zero risk to the hospital." Close on the recovered-$ counter.

## 7. What you should do (recommendation)
1. **Lock the wedge:** written-appeal closed loop on a design partner's real backlog (deployable + billable in weeks). The voice swarm is the differentiator you upsell.
2. **Sign the design partner with a BAA + a backlog feed** — that's the only thing standing between this and real recovered dollars.
3. **Lead every pitch with two numbers:** the recovered-$ and the PAVO cost-collapse. They're what no competitor can match.
4. **Build the win-corpus from day one** — it's the moat, and it only compounds with real volume.
