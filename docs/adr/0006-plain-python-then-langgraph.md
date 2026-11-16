# ADR 0006: Plain Python for the pipeline; LangGraph only for the wait for a person

- **Status:** Accepted (milestone 5; context changed by ADR 0028, see below)
- **Control:** [HM-05](../controls.md#hm-05)

## Context

When this was decided, the triage pipeline was a fixed sequence of five steps with one bounded
loop. [ADR 0028](0028-agent-picks-the-steps-code-holds-the-boundaries.md) later let a supervisor
agent choose the order, within bounds code holds. A claim sent to a person may wait days, across restarts, and the adjuster's decision has to be tied to the
evidence they saw.

## Decision

The triage pipeline is plain Python (`triage` in
[`pipeline.py`](../../hall_monitor/pipeline.py); by default the supervisor loop in
[`agentic.py`](../../hall_monitor/agentic.py), with the fixed order kept as `agentic=False`), so
every step, condition and refusal is in Python and tested directly. LangGraph is used only around it, in
[`workflow.py`](../../hall_monitor/workflow.py), for what it is good at: an `interrupt` that pauses
a claim for an adjuster, with state saved by a SQLite checkpointer, and a resume when the
decision comes in. The decision is checked in code against the adjuster's authority limit and
the hash of the evidence pack they were shown
([`governance/approval.py`](../../hall_monitor/governance/approval.py)).

## Alternatives considered

- **The whole pipeline as a LangGraph graph.** Rejected at the time: the steps were fixed, so
  a graph would have put control flow in configuration for no gain. Since ADR 0028 the
  supervisor chooses the order, which is what a graph is built for: the supervisor and each
  agent as nodes, with a node in code that applies the refusal and budget rules and picks the
  next one, would keep the boundaries in code and save state between steps. Not done yet; the
  consequences below say what that costs.
- **A hand-built pause and resume.** Rejected: persistence and resume are where hand-built
  workflow code tends to fail, and LangGraph already provides them.
- **A workflow engine (Temporal, Step Functions).** Right for production scale; more than a
  reference implementation needs.

## Consequences

- Readers can audit the decision logic without learning a framework.
- SQLite checkpoints are fine for one process; production would use Postgres.
- The supervisor's whole run is one node of the graph (`triage`), so nothing is saved between
  its steps. A triage that crashed is restarted from the start (`run --fresh`), and every agent
  it had already run is called, and paid for, again (HM-32 gap).
- The checkpoints hold the claim as sent, in plain JSON, with its result and evidence pack, and
  nothing deletes a decided claim's thread, so erasure under a data-protection request doesn't
  reach them (HM-10 gap).
- An adjuster can only decide on the evidence pack the claim was paused with; a different hash
  is refused. Checkpoints aren't tamper-evident, so the review step also checks the saved pack
  against the copy stored under its hash in the audit database
  ([ADR 0014](0014-signed-decisions-and-stored-evidence.md)).
- Decisions must be signed with the adjuster's key (ADR 0014).
