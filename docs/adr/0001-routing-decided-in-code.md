# ADR 0001: A code rule, not a model, decides who decides

- **Status:** Accepted (milestone 2, hardened after the first code review)
- **Control:** [HM-04](../controls.md#hm-04)

## Context

Every claim ends either in an automatic payment or with a human adjuster. If a model made that
call, a persuasive claim, a prompt injection or a plain model error could talk a claim past
review, and nobody could state the rule an auditor would be checking against.

## Decision

`route_claim` in [`governance/routing.py`](../../hall_monitor/governance/routing.py) is ordinary
Python. A claim goes automatically only when every one of these is false: the recommendation
isn't "pay", nothing is covered, the payout exceeds the amount claimed, the payout is above
$2,500 (`ADJUSTER_PAYOUT_THRESHOLD_USD`), there is any fraud flag or non-low risk, the critic
still disagrees or changed a decision field, or the claim text addresses the AI. Facts are also
checked against the policy record: the claim goes to a person if the claimant isn't the insured
(names compared ignoring case, accents and punctuation), the incident date is missing or outside
the coverage period, there is no policy record, or the claim isn't in USD. A claim the pipeline
couldn't finish always goes to a person (`route_stopped`).

The rule checks the facts behind a decision, not only the labels. A review found the first
version trusted the coverage label; a claim paying $0 labelled "covered" would have gone
through. It now checks the amounts as well.

## Alternatives considered

- **Let the recommender choose the route.** Rejected: it can be argued with, and its reasons
  can't be tested exhaustively.
- **A confidence score threshold.** Rejected: model confidence isn't calibrated for this, and a
  threshold on it is hard to defend to an auditor.

## Consequences

- The rule is short enough to read in a minute and is tested case by case
  (`tests/test_pipeline.py`), and the eval checks every route against the answer key.
- It is conservative by design: about 58% of test runs go to a person. The test claims are
  weighted towards edge cases, so the production rate is unknown.
- Changing the threshold is a code change, reviewed and gated like any other.

## Evidence

No claim that needed a person went through automatically in 204 scored runs
([hardened findings](../../evals/results/hardened-critic/findings.md)); the eval gate fails CI
if `unsafe_routes` is ever above 0.
