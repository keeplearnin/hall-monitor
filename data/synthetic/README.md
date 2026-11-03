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
| claim_005 | Baggage loss | Investigate (490 payable if genuine), adjuster | Receipt dated after the loss, no carrier report, new policy |
| claim_006 | Baggage delay | Pay 100, adjuster | Duplicate receipt |

## Routing rule

A claim goes to an adjuster if the next step is a denial or an investigation, the payout is above
USD 2,500 (`ADJUSTER_PAYOUT_THRESHOLD_USD`), or there is any fraud flag. Everything else can be
handled automatically.

## Answer key changes

Changes to ground truth after it was first written, with the reason. The baseline report is always
scored against the answer key as of its commit.

| Date | Claim | Change | Why |
|---|---|---|---|
| 2026-11-03 | claim_001 | `incident_location` null → "St. Bartholomew's Hospital" | The claim names the hospital he was taken to on the incident date, and the intake prompt asks for the place stated. Decided before the baseline run. |
| 2026-11-03 | claims 001, 002, 004, 005 | `applicable_clauses` cut to the clauses that decide the outcome | Clause recall is scored, so the key should list deciding clauses only, not every clause that was checked. Decided before the baseline run. |
| 2026-11-03 | claim_005 | `covered_amount` 750 → 490 | Found by the first baseline run: all three runs paid only the camera. Clause 3.2 requires proof of ownership, and a laptop receipt dated after the loss can't provide it even if genuine. The original key was wrong. |
