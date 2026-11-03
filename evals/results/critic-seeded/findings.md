# Findings: does the critic catch errors?

Written by hand after the seeded-error eval in [report.md](report.md), commit `ecd20c3`.

In the full pipeline runs the critic rarely had anything to catch. In the hardened run it raised
nothing in 102 runs, so its value was unmeasured. This eval measures it directly. It gives the
critic the answer key's own extraction, coverage and risk for each claim, either unchanged or
with one planted error of a kind seen in earlier runs. That is one critic call per case and
118 cases, for about $1.80.

## Results

| Case | Cases | Result |
|---|---|---|
| Nothing wrong: false alarms | 34 | 1 (2.9%) |
| Amount pays the full claim while the reasoning names a cap (the real slip) | 16 | 16 caught (100%) |
| Unsupported `amounts_inconsistent` flag (the real over-flagging) | 34 | 34 caught (100%) |
| Incident date the claim states left blank | 34 | 34 caught (100%) |

Each review costs about $0.015.

## What this means

**When there is an error of these kinds, the critic catches it.** That includes all 16 cases of
the failure it was built for. In the pipeline it rarely has anything to catch, because the other
agents now rarely make these mistakes: no amount slip in 360 pipeline runs since
coverage-check-v2. So the critic works as insurance. It costs about $0.015 per claim and catches
errors that the routing rule alone would let through automatically when the amount is under the
payout threshold. claim_029 is an example: a 318.40 payout instead of 200 would route
automatically.

**False alarms are rare and harmless.** The one false alarm (claim_008) pointed out that the
coverage reasoning mentions clause 2.3 while the cited clauses don't include it. That's a fair
point about the answer key's own wording. It doesn't touch the amount, so under the routing rule
it wouldn't send the claim to an adjuster.

**Recommendation: keep the critic.** For a regulated workflow, catching 100% of these planted
errors for 1.5 cents a claim is worth it. To cut cost later, it could run only on claims where a
cap, limit or deduction applies, which is where the amount slip happens.

## Limits

- The planted errors are clear-cut: a full-claim amount, a flag whose stated reason the documents
  contradict, a blank field. Subtler errors (an amount off by one depreciation year, a wrong
  clause with the right amount) would be harder, and aren't tested here.
- One run per case. With 16-34 cases per type, a 100% result is consistent with a true catch rate
  in the high 80s or above. It doesn't prove the critic is perfect.
- The critic was tested on answer-key work, which is cleaner than real agent output.
