# Baseline findings: 26 claims

What the numbers in [report.md](report.md) mean, written by hand after the run. The report is
generated and gets overwritten on every run; this file doesn't.

## Headline

| Extraction accuracy | Coverage-reasoning accuracy | Hallucination rate | Cost per claim | Human override rate |
|---|---|---|---|---|
| 98.3% | 89.7% | 0.0% | $0.022 | n/a |

26 claims x 3 trials = 78 runs, 0 errors. Single-pass pipeline (no critic, no MCP tools), the same
code as the [first baseline](../baseline/findings.md). Run on commit `ed81c1b`, which is on `main`.

**No wrong amount was ever paid automatically.** 77 of 78 runs were routed correctly. The one wrong
route sent a claim to an adjuster that could have been automatic, the safe direction. Every run
with a wrong covered amount went to an adjuster, either because of the payout threshold or because
of a fraud flag.

On the six original claims this run scored 100% extraction and 88.9% coverage reasoning, against
100% and 83.3% in the first baseline. Same code, so that difference is run-to-run variation.

## What went wrong

**1. The amount contradicts the reasoning (claim_008, 2 of 3 runs; claim_006, 1 of 3).** The same
failure as in the first baseline, now on a second claim. On claim_008 the coverage agent wrote
"the limit is $3,000, so payment is capped; see amount below", then returned 3,640, the full amount
claimed. Both runs went to an adjuster because 3,640 is over the payout threshold. This is the
critic's first job in milestone 3.

**2. One missing field cascaded into a wrong denial (claim_018, 1 of 3 runs).** Intake returned a
null policy number although the narrative states it. The pipeline then found no policy record,
and the coverage agent declined to assess the claim: "the narrative quotes WTP-PLS-200612, but
nothing confirms it exists". That run was denied instead of paying 450. It went to an adjuster
because it was a denial, but the pipeline should never have continued. Two fixes: the pipeline
should stop and ask for the policy number when the lookup fails, and the critic should check
extracted fields against the claim text.

**3. The decision label doesn't follow its own definition (claim_014, 3 of 3 runs).** The amount
was right (540 after the 100 deductible) but labelled `covered` instead of `partially_covered`. The
label is defined by the amount (equal to the total claimed, more than 0, or 0), so code can work
it out. Proposed fix: compute the label from the amount instead of asking the model.

**4. Over-sensitive flags (claim_006, 2 of 3; claim_012, 3 of 3).** On claim_006 the duplicate
receipt was flagged a second time as `amounts_inconsistent`, as before. On claim_012 the risk agent
flagged `dates_inconsistent` because the narrative reads as if the trip was cancelled on 3
September while the documents say 4 September. That pushed a clean, uncovered claim to medium
risk. Neither changed a route. Flag precision is 73%, flag recall 100%.

**5. A missed clause (claim_005, 1 of 3).** Cited 1.7 and 3.2 but not 3.3 (missing carrier
report). The amount was right.

## Extraction misses are mostly an answer-key question

12 of the 702 extraction fields were wrong. 11 of them are `incident_location`, on four claims
where the model returned null and the answer key names a place:

| Claim | Answer key | What the claim says |
|---|---|---|
| claim_011 | France | A strike by French air traffic controllers |
| claim_019 | Boston, USA | Bag delayed flying home from Rome to Boston |
| claim_021 | Edinburgh, UK | Suitcase lost on the flight to Edinburgh |
| claim_022 | Prague, Czech Republic | Bag lost on the flight to Prague |

For a lost bag, null is arguably more correct: nobody knows where it was lost. The field needs a
tighter definition in both the intake prompt and the answer key (for example, where the claimant
was when the event was discovered). That's a change to the eval, so it is left for a separate PR
rather than made after seeing these results. The twelfth miss is claim_018's policy number above.

## What went right

- All exclusions were applied correctly in every run: pre-existing condition with and without
  the Plus waiver, hazardous activity, alcohol (shown only in the hospital report), elective
  treatment, and the outbound-only and 12-hour delay rules.
- The trickier calculations were right every time: the 72-hour cancellation rule (claim_010),
  depreciation with the per-item limit (claim_021), the valuables limit (claim_022) and the
  three-part-day delay cap (claim_018, when the policy number was found).
- Both fraud cases raised every expected flag in every run. Flag recall is 100%.
- No hallucinated values in 78 runs.

## What this means for milestone 3

The critic should check, in order of value:

1. The covered amount against the limits and caps that the coverage reasoning itself names.
2. Extracted fields against the claim text, so a missing policy number is caught before coverage.
3. Each flag against the evidence for it, so one problem isn't counted twice.

Separately: stop the pipeline when the policy lookup fails, and compute the decision label in code.

## Limits

- 26 claims is better than 6 but still small. One run now moves coverage accuracy by about 1.3
  points.
- The same author wrote the policy, the claims and the answer keys.
- Cost is at list price with no prompt caching.
