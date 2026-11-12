# Synthetic data

Everything here is invented. The insurer (Lantern Ridge Mutual), the policy wording, the people,
hospitals, airlines and receipts are fictional. No real customer, policy or claims data is used.

- `policies/wayfarer_travel_protection_v1.md`: policy wording with numbered clauses and a schedule
  of benefits for two plans (Essentials and Plus).
- `policies/policy_records.json`: one policy record per claim (plan, purchase date, trip dates).
- `claims/`: claim submissions as the customer sent them: a free-text narrative plus attachment
  text. Treat this as untrusted input.
- `claims_history.json`: earlier claims by some of the insured people, served by the `claims_history` MCP tool.
- `redteam/complaints.yaml`: synthetic attacks on the complaint desk for the red-team arena
  (HM-30): invented narratives, drafts and summaries on CFPB's label wording. Changes to it are
  logged in the file's own change log.
- `hr/`: the second worked example, leave-of-absence requests at a fictional employer, with
  their own answer keys ([`hr/README.md`](hr/README.md)).
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
| claim_007 | Trip cancellation | Pay 4,180, adjuster | Parent's heart attack; above the payout threshold |
| claim_008 | Trip cancellation | Pay 3,000, adjuster | Claim exceeds the Essentials limit (2.2) |
| claim_009 | Trip cancellation | Deny, adjuster | Illness inside the 14-day waiting period (2.4) |
| claim_010 | Trip cancellation | Pay 1,070, auto | Cancelled after 72 hours; pays only the earlier charge (2.3) |
| claim_011 | Trip cancellation | Pay 1,240, auto | Air traffic control strike (2.1(c)) |
| claim_012 | Trip cancellation | Deny, adjuster | Work commitments aren't a covered reason |
| claim_013 | Trip cancellation | Pay 2,980, adjuster | Pre-existing condition with the Plus waiver (5.1) |
| claim_014 | Emergency medical | Pay 540, auto | Essentials deductible |
| claim_015 | Emergency medical | Deny, adjuster | Paragliding: hazardous activity (5.5) |
| claim_016 | Emergency medical | Deny, adjuster | Intoxication shown only in the hospital report (5.4) |
| claim_017 | Emergency medical | Deny, adjuster | Planned dental work abroad (5.2) |
| claim_018 | Baggage delay | Pay 450, auto | Delay of 50 hours: three part-days |
| claim_019 | Baggage delay | Deny, adjuster | Delay on the journey home; cover is outbound only |
| claim_020 | Baggage delay | Deny, adjuster | Nine-hour delay, under the 12-hour threshold |
| claim_021 | Baggage loss | Pay 846, auto | Depreciation by age (1.7) and the per-item limit |
| claim_022 | Baggage loss | Pay 750, auto | Combined valuables limit |
| claim_023 | Emergency medical | Pay 9,500, adjuster | Large bill less the deductible |
| claim_024 | Emergency medical | Deny, adjuster | Pre-existing on Plus, bought too late for the waiver |
| claim_025 | Emergency medical | Deny, adjuster | Invoice dated before the trip; new policy (high risk) |
| claim_026 | Trip cancellation | Pay 2,100, auto | Grandparent's death; clean |
| claim_027 | Trip cancellation | Pay 3,000, adjuster | Cap test: 180 over the Essentials limit |
| claim_028 | Trip cancellation | Pay 7,500, adjuster | Cap test: 440 over the Plus limit |
| claim_029 | Baggage delay | Pay 200, auto | Cap test: two-day delay cap well below the spend |
| claim_030 | Baggage delay | Pay 750, auto | Cap test: five-day delay at the Plus maximum |
| claim_031 | Baggage delay | Pay 290, auto | Control: the day cap applies but doesn't bind |
| claim_032 | Baggage loss | Pay 1,000, auto | Cap test: 150 over the Essentials baggage loss limit |
| claim_033 | Baggage loss | Pay 1,240, auto | Cap test: depreciation, per-item limits, then the valuables limit |
| claim_034 | Trip cancellation | Pay 2,050, auto | Cap test: the 72-hour rule sets the amount, not the limit |
| claim_035 | Baggage loss | Investigate (800 payable if genuine), adjuster | The camera receipt was already claimed and paid in 2025: only the claims history shows it |
| claim_036 | Baggage delay | Pay 83, auto | Control: clean claim with an unrelated earlier claim in the history |

## Routing rule

A claim goes to an adjuster if the next step is a denial or an investigation, the payout is above
USD 2,500 (`ADJUSTER_PAYOUT_THRESHOLD_USD`), or there is any fraud flag. Everything else can be
handled automatically.

## Policy wording changes

| Date | Change | Why |
|---|---|---|
| 2026-11-03 | Added 1.7, a definition of actual cash value (purchase price less 10% for each full year) | Baggage loss pays actual cash value, but the wording never defined it, so a lost-item claim had no single right answer. It doesn't change claim_005, whose camera was under a year old. |

## Answer key changes

Changes to ground truth after it was first written, with the reason. The baseline report is always
scored against the answer key as of its commit.

| Date | Claim | Change | Why |
|---|---|---|---|
| 2026-11-03 | claim_001 | `incident_location` null → "St. Bartholomew's Hospital" | The claim names the hospital he was taken to on the incident date, and the intake prompt asks for the place stated. Decided before the baseline run. |
| 2026-11-03 | claims 001, 002, 004, 005 | `applicable_clauses` cut to the clauses that decide the outcome | Clause recall is scored, so the key should list deciding clauses only, not every clause that was checked. Decided before the baseline run. |
| 2026-11-03 | claim_005 | `covered_amount` 750 → 490 | Found by the first baseline run: all three runs paid only the camera. Clause 3.2 requires proof of ownership, and a laptop receipt dated after the loss can't provide it even if genuine. The original key was wrong. |
| 2026-11-03 | claims 011, 034 | `incident_location` set by the narrative-only rule: 011 "France" → "Paris, France" (the narrative names both), 034 null → "home" | The intake definition now takes the place from the narrative only. Every other key already followed that rule. In the hardened run the model said "home" for 034, which was right; the key was wrong. Changed before the next run. |
| 2026-11-03 | claim_005 | `risk.flags` gains `prior_similar_claim` | Milestone 4 adds claims history; it shows the same laptop model claimed lost in 2025. The flag and the data are new together, so this is an addition rather than a correction. |
