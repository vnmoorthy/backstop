---
runbook_id: rb-aetna-co197-prior-auth
payer: Aetna
denial_code: CO-197
category: PRIOR_AUTHORIZATION
source: appeals-playbook
doc_type: runbook
carc: CO-197
win_rate: 0.71
---

# Aetna — CO-197 Precertification / Prior Authorization Absent

Aetna issues CO-197 when its system shows no precertification on file for a
service that requires one. The overturn almost always hinges on producing the
authorization reference number and proving it was active before the date of
service. Do not let the representative route this to a clinical review until the
authorization-on-file question is settled first.

## Winning rebuttal

> Prior authorization was in fact obtained for this service. Authorization
> reference number AUTH-{auth_ref} was approved on {auth_date}, which predates
> the date of service. The precertification was on file and active at the time
> of service, so CO-197 does not apply. Please reprocess the claim against the
> approved authorization rather than denying for precertification absent.

## Required evidence

- Authorization reference / certification number and approval date.
- Screenshot or fax confirmation showing the auth covered this CPT and provider.
- Date-of-service proof that the service fell inside the authorization window.

## IVR path

1. Dial the Aetna provider services line.
2. At the main menu, press `2` for "claims and authorizations".
3. Press `1` for "claim status or denial".
4. Enter the provider NPI followed by `#`.
5. Enter the member ID followed by `#`.
6. Say "precertification" or press `3` to reach the prior-authorization desk.
7. Request a representative; ask specifically for the authorization-on-file
   review, not a new precertification.
