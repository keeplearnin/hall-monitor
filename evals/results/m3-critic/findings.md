# Milestone 3 findings: critic, policy lookup gate, coverage label in code

Written by hand after two runs on the same commit, `3ff9fcb`. Each has its own generated report:
[with the critic](report.md) and [without the critic](../m3-fixes-only/report.md).

## Results

26 claims x 3 trials = 78 runs each, 0 errors.

| Run | Extraction | Coverage reasoning | Flag precision | Risk level | Routes | Cost per claim | Seconds per claim |
|---|---|---|---|---|---|---|---|
| [26-claim baseline](../baseline-26/findings.md) (`ed81c1b`) | 98.3% | 89.7% | 73.3% | 96.2% | 77/78 | $0.022 | 17.1 |
| Fixes only, no critic | 98.6% | 100.0% | 92.3% | 98.7% | 78/78 | $0.022 | 16.9 |
| Fixes and critic | 98.9% | 100.0% | 100.0% | 100.0% | 78/78 | $0.038 | 22.3 |

Hallucination rate was 0% in all three. Flag recall was 100% in all three.

## What each change did

**Coverage label in code.** On claim_014 the agent had given the right amount with the wrong label
in all three baseline runs. With the label computed from the amount, that error can no longer
happen. That alone accounts for 3 of the 8 coverage misses in the baseline.

**Policy lookup gate.** It didn't trigger in either run: intake found every policy number. In the
baseline, one claim_018 run lost the policy number and was wrongly denied. Now the same failure
would get one retry and then stop and go to an adjuster. The unit tests cover both paths.

**Critic.** It raised issues in 5 reviews across 4 runs (5.1% of runs) and never ended in
unresolved disagreement:

- **It fixed over-flagging every time.** On claim_012 (2 runs) it removed an unsupported
  `dates_inconsistent` flag. On claim_006 (1 run) it removed `amounts_inconsistent` as a duplicate
  of `duplicate_receipt`. That is the difference between 92.3% and 100% flag precision.
- **It was wrong once, then corrected itself.** On one claim_018 run it challenged the correct
  450, arguing the daily delay limit applies to each 24-hour period's purchases, not pooled across
  the delay. The coverage agent accepted that and changed to 294. On the next review the critic
  rejected 294 as an invented rule, and the answer went back to 450. See the policy wording
  problem below.

## What this run can't show

The headline failure from both baselines, an amount that contradicts its own reasoning (claim_006
paying 109.20, claim_008 paying 3,640 over its 3,000 limit), didn't happen in either run. Without
the critic it scored 100% too. So the jump from 89.7% to 100% coverage reasoning is the label fix
plus run-to-run variation, and these runs don't measure whether the critic catches that slip. The
unit tests show the loop sends such an amount back and accepts the correction. A live measurement
needs claims built to provoke the slip, or many more trials.

## The policy wording problem the critic found

Clause 3.1 pays "up to the daily amount for each full or part 24-hour period of delay". It can be
read two ways:

- **Pooled** (what the answer keys assume): the cap is the daily amount times the number of
  periods. On claim_018: 3 x 150 = 450.
- **Per period:** each period's purchases are capped separately. On claim_018: 150 + 144 = 294.

Claims 002, 006 and 018 depend on this. The fix is to make the wording explicit, then re-score.
That is a change to the eval, so it's left for a separate PR rather than made after seeing these
results.

## Cost

The critic adds about $0.016 per claim (+73%) and 5 seconds, almost all of it from one Sonnet
review per claim. Revisions are rare (5% of runs) so they add little. The review sends the full
policy wording every time, which makes it a good candidate for prompt caching in milestone 7.

## Remaining misses

All extraction misses are `incident_location` on claims 011, 019, 021 and 022, the answer-key
definition question from the 26-claim baseline. No other field was wrong in either run.

## Next

- Make clause 3.1 explicit about pooling, and define `incident_location` in the intake prompt and
  answer keys. Then re-run both configurations.
- Add claims built to provoke the amount-versus-reasoning slip, so the critic's main job can be
  measured live.
- Milestone 4: move policy data behind governed MCP tools.
