# ADR 0013: Publish every result, including failures, and check every pull request against them

- **Status:** Accepted (milestones 2 and 9)
- **Controls:** [HM-17](../controls.md#hm-17), [HM-18](../controls.md#hm-18)

## Context

Governance claims are easy to make and hard to check. The project's credibility depends on
evidence a reader can reproduce, including what went wrong.

## Decision

- All claims, policies and people are synthetic and fictional, and the limits of that are
  written down ([data README](../../data/synthetic/README.md)).
- Every eval run is committed with its raw results and a findings note, including bad runs and
  answer-key corrections.
- Every pull request runs the eval gate (`evals.gate`, thresholds in
  [`evals/gate.yaml`](../../evals/gate.yaml)): accuracy, hallucination, unsafe routes and critic
  catch rate. Results scored on agent versions that are no longer approved are flagged as stale.
  A failing gate fails CI; it blocks a merge only once branch protection requires the check,
  and it isn't on yet.
- Controls are mapped to NIST AI RMF and FINOS AIGF in
  [`controls/controls.yaml`](../../controls/controls.yaml), and tests fail if a reference goes
  stale.

## Consequences

- One person wrote the claims and the answer keys, so results are early signals, not proven
  rates. That is said wherever a number appears.
- Stale results currently warn rather than fail, because milestones 4 to 8, milestone 11 and
  the work since (the policy-record and amount routing checks, the kill switch, the tax-ID and
  bank redaction) haven't had a scored live run yet. The published routing figures were scored
  under an older routing rule; the current one is stricter. After the next baseline,
  `stale_results` switches to `fail`.
- The gate checks published results; it doesn't run the models in CI, which keeps CI free and
  deterministic. Live runs are a manual workflow.
