# Baseline eval: single-pass pipeline

Run 2026-11-03 21:07 UTC on commit `47b743c`. 6 synthetic claims x 3 trials = 18 runs, 0 errored.

Pipeline: intake, coverage check, risk and fraud, recommendation, then the routing rule. No critic loop (milestone 3) and no MCP tools (milestone 4).

## Headline

| Extraction accuracy | Coverage-reasoning accuracy | Hallucination rate | Cost per claim | Human override rate |
|---|---|---|---|---|
| 100.0% | 83.3% | 0.0% | $0.0239 | n/a (no adjusters yet) |

## Detail

| Metric | Value |
|---|---|
| Coverage decision accuracy | 100.0% |
| Covered amount accuracy | 88.9% |
| Clause recall | 97.2% |
| Risk level accuracy | 88.9% |
| Flag precision | 83.3% |
| Flag recall | 100.0% |
| Next step accuracy | 88.9% |
| Route accuracy | 100.0% |
| Seconds per claim (sum of agent calls) | 18.0 |

## By claim

Counts are correct runs out of trials.

| Claim | Scenario | Coverage | Next step | Route | Expected flags | Flags raised |
|---|---|---|---|---|---|---|
| claim_001 | Trip cancelled for appendicitis; clean, covered, below the adjuster threshold. | 3/3 | 3/3 | 3/3 | none | none |
| claim_002 | Baggage delayed 30 hours on the outbound journey; essentials only; covered. | 3/3 | 3/3 | 3/3 | none | none |
| claim_003 | Wrist fracture hiking on a marked trail; covered but above the payout threshold. | 3/3 | 3/3 | 3/3 | none | none |
| claim_004 | Cancellation due to a Crohn's flare-up; pre-existing condition, so a denial. | 3/3 | 3/3 | 3/3 | none | none |
| claim_005 | Lost bag with valuables; receipt dated after the loss, no carrier report, policy bought 2 days before travel. | 2/3 | 3/3 | 3/3 | receipt_dated_after_incident, missing_carrier_report, policy_purchased_shortly_before_incident | missing_carrier_report (3), policy_purchased_shortly_before_incident (3), receipt_dated_after_incident (3) |
| claim_006 | Short baggage delay; the same receipt is submitted twice. | 1/3 | 1/3 | 3/3 | duplicate_receipt | amounts_inconsistent (2), duplicate_receipt (3) |

## Extraction by field

| Field | Accuracy |
|---|---|
| claimant_name | 100.0% |
| policy_number | 100.0% |
| claim_type | 100.0% |
| incident_date | 100.0% |
| incident_location | 100.0% |
| claimed_items | 100.0% |
| total_claimed | 100.0% |
| currency | 100.0% |
| supporting_documents | 100.0% |

## Unsupported values

None.

## Errors

None.

## Hall passes

| Agent | Version | Model | Prompt | Manifest digest |
|---|---|---|---|---|
| intake | 0.1.0 | claude-haiku-5-5 | intake-v1 | `f20dccefa0d0` |
| coverage_check | 0.1.0 | claude-sonnet-5-5 | coverage-check-v1 | `21666b0efcfe` |
| risk_fraud | 0.1.0 | claude-haiku-5-5 | risk-fraud-v1 | `21c3d6351c15` |
| recommendation | 0.1.0 | claude-sonnet-5-5 | recommendation-v1 | `f376708f7cac` |

## Definitions

- **Extraction accuracy:** share of extraction fields matching ground truth. Claimed items compare amounts only; a location matches if it names one of the expected place's words.
- **Coverage-reasoning accuracy:** share of runs where the coverage decision is right, the covered amount is within 1 USD and every deciding clause is cited.
- **Hallucination rate:** share of checked values not traceable to the source: an extracted name, policy number, location or amount missing from the claim, a document that wasn't attached, or a clause that isn't in the policy wording.
- **Cost per claim:** list-price token cost of all four agent calls, thinking included.
- **Route:** adjuster for denials, investigations, any fraud flag, or payouts above USD 2,500; otherwise automatic.
- A run that errors scores zero on every metric except cost and time.

## Reproduce

```bash
uv run python -m hall_monitor.evals.baseline --trials 3 --out evals/results/baseline
```
