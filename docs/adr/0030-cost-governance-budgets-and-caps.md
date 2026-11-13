# ADR 0030: Cost governance: approved budgets and spending caps

- **Status:** Accepted (slice 1: approved budgets, prices and caps)
- **Control:** [HM-34](../controls.md#hm-34)

## Context

Every case's cost was already computed from token counts and recorded per agent (HM-16), and
each case had a hard budget checked before every model request (ADR 0028, review 27). But:

- **The budgets weren't approved by anyone.** They were defaults in three dataclasses
  (`Limits`, `DeskLimits`, `LeaveLimits`), and the price table every cost is computed from was a
  dict in `gateway/cost.py`. A change to either was a code edit, not an approval.
- **Nothing capped spend beyond one case.** Only the failure drills had a batch cap. The live
  CLIs and forms capped how many submissions a run takes, not dollars, and nothing held a day's
  or a month's spend (readiness checklist items 5 and 7).

As agents choose their own steps (ADR 0028), cost becomes something the model influences, so
the limits on it need the same standing as a hall pass: written down, owned, approved and
enforced in code.

## Decision

- **One approved file, `registry/budgets.yaml`.** It holds each workflow's case budget (steps,
  cost, seconds), its daily and monthly caps and its owner (a person in `owners.yaml`), and the
  price table with its date and the cache multipliers. `python -m hall_monitor.approvals`
  records its digest in `approvals.yaml`, so a change is reviewed like a hall pass. The digest is
  of the parsed content, so comments and layout aren't a change. The file is checked when it is
  read: every workflow has a budget, owners are listed people, a daily cap isn't above its
  monthly cap, and a case budget isn't above its daily cap.
- **Read at the start of every case, before any model call** (`spend.start_case`). The claims,
  desk and leave pipelines take their case budget from it; the dataclass defaults remain only for
  tests building partial limits, and a test keeps them equal to the file. A budgets file that
  isn't the approved version, or can't be read, refuses the case.
- **Prices come from the same approved file**, read again whenever it or the approvals file
  changes, so an approved price change reaches a long-running form or panel without a restart.
  If it isn't approved, no model has a price, so every case's cost is unknown and goes to a
  person (an unpriced cost already stopped the loops and counted as a failure on every path).
- **Daily and monthly caps per workflow, held with reservations** (`hall_monitor/spend.py`).
  Before its first model call a case reserves its whole case budget against its workflow's caps
  in a SQLite ledger (`var/spend.sqlite`) shared by every process on the machine, inside
  `BEGIN IMMEDIATE`, so two processes can't both take the last of a cap. When the case ends the
  reservation settles at what it cost. A crash or an interruption leaves the reservation
  counting in full, and so does a cost that can't be priced or a ledger that fails while
  settling: all err towards refusing. A ledger that can't be reached when a case starts (locked
  past its 30 s timeout, a disk error) refuses the case like a cap, blaming no agent. Days and months are UTC. The
  ledger holds workflow names, case IDs, amounts and times only.
- **A refused case goes to a person, with no model call** (the owner's choice over "don't
  start it"): a claim to an adjuster (`pipeline stopped: spend cap reached: daily claims cap of
  5.00 USD`), a complaint to specialist review (`spend_refused`, at high urgency on the agentic
  path since no agent read it), a leave request to an HR director (`spend_refused`, since the
  rules never ran), with its evidence pack stored so it can be signed. The reasons are fixed
  templates, and the panel shows them.
- **The live entry points pass the ledger**: `hall_monitor.run`, `complaints.run --live`,
  `complaints.submit --live`, `hr.run` and `hr.submit --live`. Demo and scripted runs aren't
  counted: their costs come from scripted token counts, not money spent. Tests check that each
  entry point passes the ledger, and that a form's live submission is refused at the cap.

## Alternatives considered

- **A cost block in each agent's manifest.** It would tie spend to each hall pass, but caps are
  per workflow (a case runs several agents) and every manifest's digest would change, so all
  twelve agents would need re-approval. Per-agent caps are slice 2.
- **Don't start a case at the cap** (as the drills' batch cap does). Rejected for live work: a
  case that is refused and not queued can drop out of every queue; sending it to a person fails
  safe.
- **Count spend from the audit log.** The log has each call's tokens, but a reservation has to
  be taken atomically before the case starts, across processes; a separate small ledger does
  that without touching the hash chain.

## Consequences

- A live deployment can't spend more than the approved caps in a day or a month per workflow,
  to within the requests in flight (each case's overshoot is one request, ADR 0028).
- Raising a cap, changing a price or a case budget is a reviewed approval, recorded by digest.
- At a cap, cases keep arriving with people instead of agents, so the cost of a cap is human
  work, and the panel shows the reason.

## Not yet (slices 2 and 3)

- **Per-agent caps** and **signed spend approvals**: a live run whose estimated cost passes a
  threshold needs a named approver's signed approval first. Until then the evals' live runs
  (baseline, fairness, adversarial, the seeded critic eval, the complaint evals) aren't held to
  the caps; they are run
  by a person who states the cost first (CLAUDE.md).
- **A Cost page in the panel and a `cost report` CLI**: spend against caps by workflow, agent
  and day, cost per case, and the cases a cap or budget stopped.
- The caps hold for one machine's ledger; several machines would need a shared store (as the
  panel's write actions would, milestone 15).
