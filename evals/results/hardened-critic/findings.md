# Findings: hardened pipeline, 34 claims

Written by hand after two runs on the same commit, `d083a79`. Each has a generated report:
[with the critic](report.md) and [without the critic](../hardened-no-critic/report.md).

This commit fixes the high-severity findings from two independent code reviews (routing that
trusted model labels, crashes on refusals, untrusted text that could escape its fence, lost
revision history, claim content sent to tracing, prompts not covered by the hall pass). It also
adds 8 claims built so that a cap or limit decides the amount (claims 027-034), and clarifies
clause 3.1 and the definition of `incident_location`.

## Results

34 claims x 3 trials = 102 runs each, 0 errors.

| Run | Extraction | Coverage reasoning | Clause precision | Flag precision | Routes | Unsafe routes | Cost per claim |
|---|---|---|---|---|---|---|---|
| No critic | 98.5% | 98.0% | 74.8% | 82.1% | 99/102 | 0 | $0.024 |
| With critic | 98.7% | 98.0% | 73.5% | 92.3% | 100/102 | 0 | $0.039 |

An unsafe route is a claim that should have gone to an adjuster but went through automatically.
There were none. Every wrong route sent a claim to an adjuster unnecessarily.

## The critic raised nothing in this run

In 102 runs the critic approved every first answer: no issues, no revisions. So the "with
critic" run produced the same kind of output as the "no critic" run, and **every difference
between the two rows is run-to-run variation**, including flag precision (82% vs 92%) and routes.
That is a useful measure of noise: on 34 claims x 3 trials, a metric can move by around 10 points
with nothing changed.

It also means:

- Its main job is still unmeasured. No amount contradicted its reasoning in either run, including
  all 48 runs of the cap-test claims (24 each). The slip happened 3 times in 78 runs in the
  26-claim baseline and not once in 360 runs since coverage-check-v2. Removing the decision label
  from the coverage prompt may be why, but these runs can't show that.
- It missed one thing it caught in milestone 3: an unsupported `dates_inconsistent` flag
  (claim_029, 1 run), which sent a clean claim to an adjuster.

Whether the critic is worth $0.015 per claim is now an open question. It needs a test set with
seeded errors (wrong amounts, extra flags, missing fields) so it can be measured directly.

## The policy lookup gate worked

On 2 runs (claim_018 without the critic, claim_029 with it), intake returned no policy number,
then again after being told exactly what was missing. Both narratives start "Name, WTP-..." and
never use the word "policy". In the 26-claim baseline the same failure produced a wrong denial.
Here both claims stopped and went to an adjuster with the reason recorded. They count as wrong in
the scores. A code check for the policy-number format would catch these before intake is retried.

## Clause precision is now visible

About 74% of cited clauses are deciding clauses; the rest are extra. Recall is 98.5%. Coverage
reasoning accuracy is still defined on recall, so the over-citing doesn't lower it.

## Extraction misses come from the answer key, not the model

All extraction misses are `incident_location`, and most come from the new definition. It asks for
"where the illness or injury happened", and the model now gives places the answer key has as
null:

| Claim | Model | Answer key | Note |
|---|---|---|---|
| claim_010, 027, 028 | the hospital named in the documents | null | claim_001's key does use the hospital, so the keys are inconsistent |
| claim_034 | "home" | null | The narrative says she fell "at home". The model is right |
| claim_033 | null (1 run) | Vienna, Austria | Model miss |

The answer keys weren't updated to match the new definition. That's my error, and it will be
fixed in the next change with a stricter rule (the place the narrative itself names).

## Next

- Seeded-error claims to measure the critic directly, then decide whether to keep it.
- A code check for the policy-number format before intake's retry.
- Make the `incident_location` answer keys follow the definition.
- Milestone 4: governed MCP tools.
