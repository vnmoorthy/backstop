---
runbook_id: rb-uhc-co45-fee-schedule
payer: UnitedHealthcare
denial_code: CO-45
category: FEE_SCHEDULE
source: appeals-playbook
doc_type: runbook
carc: CO-45
win_rate: 0.34
---

# UnitedHealthcare — CO-45 Charge Exceeds Fee Schedule

CO-45 is a contractual adjustment: the billed charge exceeds the contracted
allowable or maximum fee-schedule amount. For a participating provider this is
usually a legitimate write-off and must never be balance-billed to the member.
The call is worth making only when the allowable looks wrong against the
contracted rate — for example a downcoded fee schedule or a stale rate load.

## Winning rebuttal

> The adjustment under CO-45 reflects the contracted fee-schedule allowable. For
> a participating provider this amount is a write-off and is not the member's
> responsibility. If, however, the allowable applied is lower than our contracted
> rate of {contracted_rate} for this code, the fee schedule was loaded
> incorrectly and we ask that the claim be reprocessed at the correct
> contracted amount. Otherwise we accept the contractual adjustment.

## Required evidence

- The contracted rate for the CPT/HCPCS from the participation agreement.
- The remittance line showing the allowable UHC actually applied.
- The fee-schedule effective date to detect a stale rate load.

## IVR path

1. Dial the UnitedHealthcare provider line.
2. Press `1` for English.
3. Press `3` for "claims".
4. Say "payment amount question" or press `4`.
5. Enter the tax ID or NPI followed by `#`.
6. Enter the member ID as prompted.
7. Request a representative and ask for the allowed-amount and fee-schedule
   detail for the specific claim line.
