---
runbook_id: rb-cigna-co16-missing-info
payer: Cigna
denial_code: CO-16
category: MISSING_INFORMATION
source: appeals-playbook
doc_type: runbook
carc: CO-16
win_rate: 0.74
---

# Cigna — CO-16 Claim Lacks Information

CO-16 means the claim is missing information or carries a submission/billing
error; the accompanying RARC (for example N130, M127, or a missing-NDC remark)
names the exact element. These are the cheapest to win: identify the flagged
field from the remark code, correct it, and resubmit. The phone call only
confirms which remark drove the denial so the corrected resubmission lands.

## Winning rebuttal

> The information identified as missing has been supplied. The remark code
> attached to this CO-16 denial points to {missing_field}, which is now included
> on the corrected claim. With the required element present, the submission is
> complete and payable. We are resubmitting the corrected claim for adjudication
> rather than appealing, since the only defect was the missing information now
> provided.

## Required evidence

- The RARC remark code(s) paired with the CO-16 on the remittance.
- The corrected value for the flagged field (NDC, referring NPI, units, etc.).
- A corrected-claim resubmission with the frequency code set appropriately.

## IVR path

1. Dial the Cigna provider services line.
2. Press `1` for "providers".
3. Press `2` for "claim status".
4. Enter the tax ID number followed by `#`.
5. Enter the patient member ID followed by `#`.
6. Say "denied claim" and then "remark code detail".
7. Ask the representative to read back every CARC and RARC on the line so the
   exact missing element is confirmed before resubmitting.
