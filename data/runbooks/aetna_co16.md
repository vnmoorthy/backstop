---
runbook_id: RB-AETNA-CO16
payer: Aetna
denial_code: CO-16
title: Aetna CO-16 — Claim lacks information / submission error
win_rate: 0.61
last_verified: 2026-04-21
---

# Aetna CO-16 — "Claim/service lacks information or has a submission error"

CO-16 is the catch-all "we need more information" denial and almost always
carries one or more **remark codes (RARC)** that say *what* is missing. The
recovery is to extract the specific remark code from the rep, supply exactly
that data element, and request a reprocess — never resubmit blind.

## The winning rebuttal (magic words)

> **"Read me the remark code attached to the CO-16, then tell me the single
> data element that resolves it — I'll correct that field and you reprocess."**

Most Aetna CO-16 lines resolve on a missing/mismatched **rendering NPI**, an
absent **referring-provider NPI**, or a **missing modifier**. Pin the rep to the
exact remark code (e.g. `N286` referring-provider identifier missing) so the fix
is surgical. In the seeded corpus this targeted-correction approach overturns
**~61%** of CO-16 lines on the first reprocess.

## IVR path (provider services / billing)

`Main menu → press 3 (claims) → enter provider tax ID → ask for the billing/EDI
remark codes on the denied line`

Ask the rep, in order:
1. "What RARC remark code is attached to this CO-16?"
2. "Which exact field is the system flagging as missing or invalid?"
3. Supply the corrected element verbatim; confirm the rep can reprocess without
   a full corrected-claim resubmission.
4. Capture the reprocess reference number.

## Why it overturns

CO-16 is procedural. The clinical service is not in dispute; one identifier or
modifier is missing or mismatched. Supplying the precise element the remark code
names removes the only stated basis for the denial.

## Precedent

- CMS RARC list: CO-16 must be paired with at least one remark code naming the
  deficiency. *(source: RB-AETNA-CO16)*
- Companion runbook: `aetna_co197.md` (authorization-absent denials).
