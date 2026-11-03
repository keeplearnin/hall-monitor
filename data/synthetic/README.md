# Synthetic data

Everything here is invented. The insurer (Lantern Ridge Mutual), the policy wording, the people,
hospitals, airlines and receipts are fictional. No real customer, policy or claims data is used.

- `policies/wayfarer_travel_protection_v1.md`: policy wording with numbered clauses and a schedule
  of benefits for two plans (Essentials and Plus).
- `policies/policy_records.json`: one policy record per claim (plan, purchase date, trip dates).
- `claims/`: claim submissions as the customer sent them: a free-text narrative plus attachment
  text. Treat this as untrusted input.
- `ground_truth/`: the expected intake extraction, coverage decision, risk flags, next step and
  route for each claim, with the reasoning written out.

## Scenarios

| Claim | Type | Expected outcome | Why it's here |
|---|---|---|---|
| claim_001 | Trip cancellation | Pay 1,992, auto | Clean baseline |
| claim_002 | Baggage delay | Pay 282.49, auto | Daily limits and part-days |
| claim_003 | Emergency medical | Pay 6,480, adjuster | Over the payout threshold; hazardous-activity exclusion doesn't apply |
| claim_004 | Trip cancellation | Deny, adjuster | Pre-existing condition defined by a medication change |
| claim_005 | Baggage loss | Investigate, adjuster | Receipt dated after the loss, no carrier report, new policy |
| claim_006 | Baggage delay | Pay 100, adjuster | Duplicate receipt |

## Routing rule

A claim goes to an adjuster if the next step is a denial or an investigation, the payout is above
USD 2,500 (`ADJUSTER_PAYOUT_THRESHOLD_USD`), or there is any fraud flag. Everything else can be
handled automatically.
