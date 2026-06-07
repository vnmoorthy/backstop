---
runbook_id: RB-AETNA-CO197
payer: Aetna
denial_code: CO-197
title: Aetna CO-197 — Precertification/authorization absent
win_rate: 0.73
last_verified: 2026-05-02
---

# Aetna CO-197 — "Precertification / authorization absent"

CO-197 means the payer's adjudication system found **no prior authorization
on file** for the billed line. On an Aetna provider-services call this is, in the
large majority of cases, a *lookup* failure rather than a genuine missing auth:
the rep searches the claim by the **billing NPI** (the group/practice number),
but Aetna files the precertification under the **rendering NPI** (the individual
servicing provider). The auth exists; the rep is looking in the wrong index.

## The winning rebuttal (magic words)

> **"Please run the auth-on-file lookup by the RENDERING NPI, not the billing
> NPI."**

Give the rep the rendering NPI explicitly and ask them to re-query. In the
seeded corpus this single move flips the call in **~73%** of CO-197 Aetna cases:
the rep finds the authorization (e.g. `A4471`), confirms it covers the date of
service, and sends the claim back to reprocess with a reference number.

Always capture the reference number the rep gives you when they reprocess.

## IVR path (provider services)

`Main menu → press 3 (claims) → enter provider tax ID → hold for a rep`

Ask the rep, in order:
1. Confirm the denial code on file (expect CO-197).
2. "Which NPI did the system search for the authorization?" — if billing NPI,
   that is the bug.
3. "Please re-run the auth lookup by the rendering NPI `<rendering_npi>`."
4. On a hit: get the auth number, confirm the DOS coverage, get a reprocess
   reference number.

## Why it overturns

The denial is administratively, not clinically, grounded. The precertification
was obtained before the visit; it simply was not surfaced by the billing-NPI
query the adjudicator ran. The records desk and the prior-authorization desk
both hold the same auth under the rendering provider — that cross-desk
confirmation is the contradiction that defeats "no auth on file."

## Precedent

- Aetna provider manual: auth records are keyed to the servicing/rendering
  provider; group-level billing NPI queries can miss them. *(source: RB-AETNA-CO197)*
- Internal recovery log: 73% overturn on rendering-NPI re-lookup across the
  seeded CO-197 backlog.
- Companion runbooks: `aetna_co16.md` (missing-info denials),
  `uhc_co197.md` (same lookup defect at UnitedHealthcare).
