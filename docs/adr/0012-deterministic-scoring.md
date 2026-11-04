# ADR 0012: Results are scored by code against answer keys, not by an LLM judge

- **Status:** Accepted (milestone 2)
- **Controls:** [HM-12](../controls.md#hm-12), [HM-13](../controls.md#hm-13)

## Context

Using a model to grade a model is common but hard to defend: the judge has its own errors and
biases, and its scores move when it is updated.

## Decision

[`evals/scoring.py`](../../hall_monitor/evals/scoring.py) scores each run against a hand-written
answer key: field-by-field extraction, coverage outcome and amount, clause precision, routing,
and a grounding check that every name, amount and clause the agents used appears in the claim or
the policy wording. Fuzzy matches are narrow and tested (for example, place names). Raw outputs
are saved so any score can be re-checked.

## Consequences

- Scores are reproducible and cheap to recompute.
- Free-text quality (how good an explanation reads) isn't scored.
- The answer keys can be wrong. Corrections are published with the reason, for example claim
  005's amount and the location rule.
