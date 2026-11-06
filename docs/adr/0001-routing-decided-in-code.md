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
the coverage period, there is no policy record, or the claim isn't in USD. The amounts are traced
too: each claimed item must match its own occurrence of that figure in the claim documents, the
total claimed must appear in them as well, and the items must add up to the total. Only a
figure the documents mark as US dollars counts ($, US$ or USD beside it on the same line, with no
other currency code or symbol, and no minus sign or dash, next to it), and never part of a date, a
time or a reference. Another currency is recognised before the mark or after the figure: any
three-capital code but USD, and the dollar and peso currencies' codes and prefixes in any case (HK,
NZ, cad, Cdn and the like), with spaces, a colon or brackets between. An item invented,
inflated, counted twice or copied from a total line then goes to a person even when coverage pays it in full, because the new total isn't in the
documents. A year or a time read as the whole claim goes to a person because it isn't written
as dollars, and so does a claim written as EUR 450, CAD $450, HK $450, (CAD) $450 or $450 cad
that the intake labels USD. A bare "$" meant as another currency, one beside a
code not in the list, or one whose currency is named only in a header still counts as USD, and a
refund written as ($20.00) traces as $20 (HM-04 gaps). (The first version of the trace
matched bare figures, and a review found that a year from a date, taken as the only item, the
total and the payout, traced and was paid automatically on all 16 automatic test claims.) A claim the pipeline couldn't finish always goes to a person
(`route_stopped`).

The rule checks the facts behind a decision, not only the labels. A review found the first
version trusted the coverage label; a claim paying $0 labelled "covered" would have gone
through. It now checks the amounts as well. Phase B's fault-injection tests
(`tests/test_agent_faults.py`) bend each agent's answer on every automatically routed test claim
and found that invented amounts passed; the amount trace was added for that. The first version
only asked whether each figure appeared anywhere, and the pre-PR review showed a duplicated item
or a total line counted as an item still passed; tracing the total closed that.

What it can't catch is a wrong answer that agrees with every fact it checks: a risk step that
misses a fraud signal, or a coverage step that ignores a limit but pays no more than was
claimed. Those tests pin both as known limits; the critic, the evals and the adjuster are what
stand against them. The amount trace has a limit too: a figure written in both the narrative and
a receipt counts as two occurrences, so a wrong set of items whose total happens to appear in the
documents would pass it.

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
- Changing the threshold is a code change: CI runs the tests and the eval gate on it like any
  other, but nothing blocks the merge until branch protection is on, and it isn't yet.

## Evidence

No claim that needed a person went through automatically in 204 scored runs
([hardened findings](../../evals/results/hardened-critic/findings.md)); the eval gate fails CI
if `unsafe_routes` is ever above 0.
