# Baseline findings

What the numbers in [report.md](report.md) mean, written by hand after the run. The report itself is
generated and gets overwritten on every run; this file doesn't.

## Headline

| Extraction accuracy | Coverage-reasoning accuracy | Hallucination rate | Cost per claim | Human override rate |
|---|---|---|---|---|
| 100.0% | 83.3% | 0.0% | $0.024 | n/a |

6 claims x 3 trials, single-pass pipeline (no critic, no MCP tools), commit `47b743c`.

**Every claim was routed correctly in all 18 runs.** Each wrong answer below still went to an
adjuster, because the routing rule is code that looks at fraud flags and payout size, not at the
model's own confidence.

## What went wrong

**1. The covered amount contradicts the reasoning (claim_006, 2 of 3 runs).** The coverage agent
wrote that the 109.20 of eligible purchases is "capped at 100 for one day", then returned
`covered_amount: 109.20`. The reasoning is right and the number is wrong. This is exactly what the
critic agent in milestone 3 is for: checking the output against its own reasoning and the source
documents.

**2. One problem counted as two flags, which escalated the risk (claim_006, 2 of 3 runs).** The risk
agent flagged the duplicate receipt correctly, then also flagged `amounts_inconsistent` for the
same duplicate. Two flags pushed the risk level from medium to high and the recommendation from
pay to investigate. Flag precision is 83% for this reason alone. Possible fixes are a prompt
rule that each problem gets one flag, or a critic check.

**3. A deciding clause was left out (claim_005, 1 of 3 runs).** The agent cited 3.2 but not 3.3
(missing carrier report), although the amount was right.

## What went right

- Extraction was perfect across all 18 runs, including the duplicate receipt, which intake
  correctly reported as claimed twice.
- No hallucinated values: every name, policy number, amount, document and clause was traceable
  to the claim or the policy wording.
- The pre-existing-condition denial (claim_004) was right every time. That depends on working out
  that a medication change 43 days before purchase falls inside the 90-day window.
- The fraud case (claim_005) raised all three expected flags in every run.

## Changes to the answer key

The first run found an error in the answer key, not in the model: claim_005 expected 750, but
clause 3.2 requires proof of ownership, and a receipt dated after the loss can't provide it. All
three runs paid 490 for the camera only, which is the correct reading. The key was corrected and
the baseline run again. Every change is listed in
[data/synthetic/README.md](../../../data/synthetic/README.md#answer-key-changes).

## Limits of this baseline

- **Six claims is a small sample.** One run is worth 5.6 points of coverage accuracy. These numbers
  show which kinds of failure occur, not reliable rates.
- **Same author.** The same person (with Claude) wrote the policy, the claims and the answer key,
  so the scenarios may suit the prompts. An outside reviewer is planned for milestone 10.
- **Cost is at list price** with no prompt caching. The policy wording is sent to the coverage
  agent on every claim and could be cached (milestone 7).
- **Not covered yet:** prompt injection, PII redaction and fairness (milestone 8).
