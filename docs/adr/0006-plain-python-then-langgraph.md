# ADR 0006: Plain Python for the pipeline; LangGraph only for the wait for a person

- **Status:** Accepted (milestone 5)
- **Control:** [HM-05](../controls.md#hm-05)

## Context

The triage pipeline is a fixed sequence of five steps with one bounded loop. A claim sent to a
person may wait days, across restarts, and the adjuster's decision has to be tied to the
evidence they saw.

## Decision

The triage pipeline is a plain Python function (`triage` in
[`pipeline.py`](../../hall_monitor/pipeline.py)), so every step, condition and loop is visible in
one file and tested directly. LangGraph is used only around it, in
[`workflow.py`](../../hall_monitor/workflow.py), for what it is good at: an `interrupt` that pauses
a claim for an adjuster, with state saved by a SQLite checkpointer, and a resume when the
decision comes in. The decision is checked in code against the adjuster's authority limit and
the hash of the evidence pack they were shown
([`governance/approval.py`](../../hall_monitor/governance/approval.py)).

## Alternatives considered

- **The whole pipeline as a LangGraph graph.** Rejected for now: it would put control flow
  in graph configuration for no gain, since the steps are fixed.
- **A hand-built pause and resume.** Rejected: persistence and resume are where hand-built
  workflow code tends to fail, and LangGraph already provides them.
- **A workflow engine (Temporal, Step Functions).** Right for production scale; more than a
  reference implementation needs.

## Consequences

- Readers can audit the decision logic without learning a framework.
- SQLite checkpoints are fine for one process; production would use Postgres.
- An adjuster can only decide on the evidence pack the claim was paused with; a different hash
  is refused. The audit log keeps the hash; the pack itself is in the checkpoint store, which
  isn't tamper-evident.
- Adjuster IDs are checked against `registry/adjusters.yaml`, not authenticated. A production
  version would take the identity from the firm's sign-on.
