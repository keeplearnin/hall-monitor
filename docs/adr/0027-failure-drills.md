# ADR 0027: Fail many agents at once on purpose, and check the outcome in code

- **Status:** Accepted (failure drills, first slice)
- **Control:** [HM-31](../controls.md#hm-31)

## Context

Every control is tested one failure at a time on one claim. In production, failures arrive
together: many claims in flight, an owner's stop while some are halfway through, API errors, a
crash after a log write, a retry that submits a claim twice, a budget running out. Whether the
controls still hold together is a separate question from whether each holds alone
([design](../failure-drills-design.md)).

## Decision

- **A drill runner in the package**, `python -m hall_monitor.drills`, running the synthetic
  claims through the real claims workflow on threads, with only the model scripted.
- **Failures on a seeded plan.** A share of the cases get one fault each, the kinds taken in
  turn so each appears; the owner's stop comes when two thirds of the cases have begun; an
  optional cost cap.
- **Its own registry copy, log and checkpoints.** The stop is written to the copy; the shipped
  registry is never touched.
- **Checks read the audit log**, not the runner's counts: failures end with a person, nothing is
  left with no one, clean cases are handled normally, nothing is triaged or decided twice, every
  failure shows in the log, cost stays under the ceiling and the cap, and the log verifies.
- **The cap is held with reservations.** A case reserves a ceiling before it starts and settles
  its logged cost after, so cases running together can't pass the cap; a refused case is logged
  as `batch_budget_reached`.
- **Mutation tests** show the checks fail when routing lets everything through, when a stopped
  claim is left with no one, and when the cap never refuses.
- **A drill always reports.** An unforeseen error in one case is recorded by its type and the
  case is flagged; cases in flight at the stop are counted, with how many were then decided
  automatically (the HM-19 gap).

## Alternatives considered

- **More unit tests, one per combination.** Rejected: the point is failures overlapping in time,
  which unit tests don't produce.
- **Run the drill against the live API.** Not now: it would cost money each run and the faults
  would no longer be ours to place. A live drill can come once live evals run.
- **Separate processes instead of threads.** Closer to production, slower and harder to
  reproduce; the once-only checks already have a multi-process test (HM-05).

## Consequences

The drill shows that the controls hold together under the faults it injects, with a scripted
model, in one process. It says nothing about the agents' accuracy. Results vary run to run with
thread timing (which cases are in flight at the stop, how many the cap refuses); the checks must
pass every time. The cap belongs to the drill runner; the claims CLI has no batch cap yet.
