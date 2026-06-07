---
runbook_id: RB-CIGNA-CO50
payer: Cigna
denial_code: CO-50
title: Cigna CO-50 — Not deemed a medical necessity
win_rate: 0.61
last_verified: 2026-05-06
---

# Cigna CO-50 — "These are non-covered services because this is not deemed a medical necessity"

CO-50 is a **medical-necessity** denial: Cigna's adjudication concluded the
billed service was not medically necessary as submitted. Unlike CO-197 (a
lookup defect) this denial is *clinically* grounded, so the recovery is to put
the clinical record back in front of the reviewer — specifically the two facts
the payer's own medical policy keys on: the **documented prior failure of
conservative therapy** and the **explicit coverage (LCD / Cigna medical policy)
criterion the service meets**. The chart contains both; they were simply not
read into the necessity review.

## The winning rebuttal (magic words)

> **"The chart documents failed conservative therapy on file, and the service
> meets the Cigna medical-coverage-policy / LCD criterion — please route this
> for clinical reconsideration against that policy, not an administrative
> reject."**

Cite the conservative-care note by date (e.g. *6 weeks of PT and NSAIDs failed,
documented 2026-03-30*) and name the specific policy criterion met. In the
seeded corpus this targeted clinical rebuttal — naming the prior-failed-therapy
note **and** the LCD/policy criterion — overturns **~61%** of CO-50 Cigna lines
on first-level reconsideration.

Always capture the reconsideration / clinical-review reference number.

## IVR path (provider services)

`Main menu → press 2 (claims status) → enter provider Tax ID → say "medical
necessity / clinical review" → hold for a rep`

Ask the rep, in order:
1. Confirm the denial code on file (expect CO-50) and which medical policy /
   LCD the reviewer applied.
2. "Does the policy require documented failure of conservative therapy?" — if
   yes, that note is on file.
3. "The chart documents failed conservative therapy dated `<note_date>` and the
   service meets the policy criterion — please route for clinical
   reconsideration against that policy."
4. Capture the reconsideration reference number and the address/portal for the
   clinical records, if the reviewer requests resubmission of the note.

## Why it overturns

CO-50 turns on whether the necessity criteria in Cigna's own coverage policy
were met. When the chart already documents the prior-failed-conservative-therapy
step and the qualifying clinical criterion, the denial rests on an incomplete
read of the record, not on a genuine policy gap. Supplying the dated note and
naming the criterion removes the stated basis for "not medically necessary."

## Precedent

- Cigna medical coverage policy / applicable LCD: medical necessity for the
  service requires documented failure of conservative management; the criterion
  is met by the on-file note. *(source: RB-CIGNA-CO50)*
- Internal recovery log: 61% overturn on clinical reconsideration when the
  failed-conservative-therapy note and the policy criterion are both cited.
- Companion runbooks: `aetna_co16.md` (missing-info denials),
  `uhc_co197.md` (authorization-absent / lookup defect).
