# ADR 0011: Cheaper-model routing is built but off until it is evaluated

- **Status:** Accepted (milestone 7)
- **Control:** [HM-16](../controls.md#hm-16)

## Context

Most claims are small. Running coverage and the recommendation on Haiku instead of Sonnet for
claims under $1,000 could cut cost, but nobody has measured whether accuracy holds.

## Decision

A manifest may name an approved `economy_model` and an `economy_max_claim_usd` limit. A code
rule (`_economy` in [`pipeline.py`](../../hall_monitor/pipeline.py)) uses it only for claims
within the limit, and critic-driven revisions always go back to the main model. The feature is
off unless `--model-routing` is passed, and stays off until a scored run shows accuracy holds.
Cost per claim is recorded on every result and in the audit log, prompt caching included.

## Consequences

- Savings are not claimed until they are measured alongside accuracy.
- The economy model is named in the manifest, so it is covered by the approval hashes.
- Turning it on is a reviewed change backed by published results, not a configuration flip.
