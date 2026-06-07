---
runbook_id: rb-uhc-co197-prior-auth
payer: UnitedHealthcare
denial_code: CO-197
category: PRIOR_AUTHORIZATION
source: appeals-playbook
doc_type: runbook
carc: CO-197
win_rate: 0.66
---

# UnitedHealthcare — CO-197 Prior Authorization Not Obtained

UnitedHealthcare denies with CO-197 when the prior authorization number is
missing, expired, or attached to a different rendering provider. The strongest
overturn cites the active authorization and, where UHC's own portal shows the
auth, references the portal confirmation number to defeat the "authorization not
obtained" finding.

## Winning rebuttal

> The prior authorization was obtained and remains valid. Authorization
> {auth_ref} was approved through the UnitedHealthcare provider portal for this
> exact procedure and rendering provider, effective before the date of service.
> Because a valid precertification was on file, the CO-197 denial is in error.
> We request reprocessing under the approved authorization on the original
> claim.

## Required evidence

- UHC portal authorization confirmation number and effective dates.
- Proof the rendering provider matches the authorized provider.
- The CPT/HCPCS code listed on the approved authorization.

## IVR path

1. Dial the UnitedHealthcare provider line.
2. Press `1` for English.
3. Press `3` for "claims".
4. Say "denied claim" when prompted, or press `2`.
5. Enter the tax ID or NPI followed by `#`.
6. Enter the member ID and date of birth as prompted.
7. Say "prior authorization" to be transferred to the precertification queue.
8. Hold for a live representative and request the authorization-on-file review.
