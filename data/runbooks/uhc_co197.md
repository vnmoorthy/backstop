---
runbook_id: RB-UHC-CO197
payer: UnitedHealthcare
denial_code: CO-197
title: UnitedHealthcare CO-197 — Precertification/authorization absent
win_rate: 0.69
last_verified: 2026-05-04
---

# UnitedHealthcare CO-197 — "Precertification / authorization absent"

CO-197 means UnitedHealthcare's adjudication system found **no prior
authorization on file** for the billed line. As with Aetna, on a UHC
provider-services call this is, in the large majority of cases, a *lookup*
failure rather than a genuine missing auth: the rep queries the claim by the
**billing NPI** (the group/practice / Tax ID number), but UHC files the
notification/precertification under the **rendering NPI** (the individual
servicing provider). The auth exists; the rep is searching the wrong index.

## The winning rebuttal (magic words)

> **"Please run the auth-on-file lookup by the RENDERING NPI, not the billing
> NPI — the notification was filed under the servicing provider."**

Give the rep the rendering NPI explicitly and ask them to re-query the
authorization/notification record. In the seeded corpus this single move flips
the call in **~69%** of CO-197 UHC cases: the rep finds the authorization
(e.g. `UHC-AUTH-7783`), confirms it covers the date of service, and routes the
claim back to reprocess with a reference number.

Always capture the reference number the rep gives you when they reprocess.

## IVR path (provider services)

`Main menu → press 1 (claims & benefits) → enter provider Tax ID / NPI → say
"provider services" → hold for a rep`

Ask the rep, in order:
1. Confirm the denial code on file (expect CO-197).
2. "Which NPI did the system search for the authorization or notification?" —
   if billing NPI, that is the bug.
3. "Please re-run the auth/notification lookup by the rendering NPI
   `<rendering_npi>`."
4. On a hit: get the auth/notification number, confirm the DOS coverage, get a
   reprocess reference number.

## Why it overturns

The denial is administratively, not clinically, grounded. The notification was
on file before the claim adjudicated; it simply was not surfaced by the
billing-NPI query the adjudicator ran. UHC's notification desk and the claims
desk both hold the same record under the rendering provider — that cross-desk
confirmation is the contradiction that defeats "no auth on file."

## Precedent

- UHC provider administrative guide: advance notification / prior-auth records
  are keyed to the rendering/servicing provider; group-level Tax ID or billing
  NPI queries can miss them. *(source: RB-UHC-CO197)*
- Internal recovery log: 69% overturn on rendering-NPI re-lookup across the
  seeded CO-197 backlog.
- Companion runbooks: `aetna_co197.md` (same lookup defect at Aetna),
  `cigna_co50.md` (Cigna medical-necessity denials).
