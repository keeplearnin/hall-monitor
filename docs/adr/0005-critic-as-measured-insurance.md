# ADR 0005: A critic agent checks the work, and its value is measured

- **Status:** Accepted (milestone 3)
- **Control:** [HM-07](../controls.md#hm-07)

## Context

An early run said a payment was "capped at $3,000" and then paid $3,640. Errors like that
won't be caught by the routing rule if the amount is under the threshold. A second model can
check the first, but it costs money and can raise false alarms, so its value has to be shown,
not assumed.

## Decision

A critic (Sonnet 5.5) compares intake, coverage and fraud work with the claim documents and the
policy wording. Problems go back to the step that made them, for up to two revisions
(`MAX_REVISIONS` in [`pipeline.py`](../../hall_monitor/pipeline.py)). Every revision is kept. If a
revision changes a decision field (amount, coverage decision, risk level or flags, policy
number, claim type, incident date, total claimed), or the critic still disagrees, the claim goes
to a person ([ADR 0001](0001-routing-decided-in-code.md)). The coverage label is also set in code
from the amount, so the model can't contradict itself there.

## Alternatives considered

- **No critic, rely on routing.** Cheaper, but small wrong payments would go through.
- **Let the critic correct the answer directly.** Rejected: a silent correction hides that the
  first answer was wrong. Corrections go back to the original step and are recorded.

## Consequences

- About 1.5 cents a claim more.
- A seeded-error eval measures the critic directly: it caught 84 of 84 planted errors, with 1
  false alarm in 34 clean cases ([findings](../../evals/results/critic-seeded/findings.md)). In
  the end-to-end runs it found few real errors, because code fixes had already removed most of
  them; the critic is kept as insurance against regressions, with that measured catch rate.
- The CI gate fails if the critic's catch rate falls below 90% on any planted-error type.
