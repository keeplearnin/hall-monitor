# ADR 0028: Agents act within their grant; a handoff is a tool that starts a governed run

- **Status:** Accepted (complaint crew, second slice)
- **Control:** [HM-32](../controls.md#hm-32)

## Context

[ADR 0025](0025-delegated-passes-only-narrow.md) made grants: passes for one case that only
narrow when work is handed down. Nothing ran under them. To be agentic, a lead agent has to
decide for itself to call tools and to ask a helper for help, and the controls have to hold
while it does: its reach, its spending, and the stop list.

## Decision

- **One call path.** `run_agent`, which every agent already uses, takes the grant. A crew agent
  (one that can delegate or be delegated to) is refused before any model call without a grant
  of its own.
- **Every round checks the grant.** Before each model call the book re-checks every agent in the
  grant's chain, so a stop on the lead halts a helper between steps.
- **Spending is charged to the grant.** Each model call is priced from its usage and charged.
  Once a grant's own spending plus what it has handed on passes its allotment, the run stops and
  raises; the caller sends the case to a person. An unpriced model can't run under a grant.
  Spending also reduces what a grant can hand on.
- **A handoff is a tool, not a message.** A lead is offered `hand_off` with the helpers its hall
  pass names. Calling it asks the book for a child grant and starts the helper's own governed
  run in this process. It is not an MCP tool, never reaches the gateway and sends nothing (HM-25).
- **Code sets a helper's allotment**; the lead names only the helper and the task, and any other
  input is refused and logged.
- **A task carries every data class its writer holds.** A lead's free text can repeat anything
  it has seen, so a helper is handed work only if its hall pass allows all of the lead's data
  classes. A helper meant to see less gets its input from code.
- **What comes back is data.** The task reaches the helper fenced and escaped; the helper's
  validated answer returns as a tool result; a helper's failure returns its error type only.
- **The caller sees every handoff.** Each one's outcome (answered, refused or failed, with the
  helper's call) is kept on the runner, so a lead's answer that rests on a failed helper can be
  marked incomplete rather than passed off as whole. A grant with nothing left makes no call,
  and a second handoff to the same helper gets a fresh grant.

## Alternatives considered

- **A separate agent loop beside `run_agent`.** Rejected: two call paths means two places for a
  hall-pass, approval, data-class or stop check to be missed.
- **Let the lead choose a helper's budget.** Rejected: a lead could hand its whole allotment to
  one helper, or be talked into it by injected text.
- **Classify a task by scanning it for personal data.** Rejected for now: detection misses
  things, and a miss would leak data to a helper not approved for it.

## Consequences

Agents can now plan and hand work on, inside the controls, but no crew agents exist yet and
nothing has run live: tested with scripted clients only. Spending is charged after each call,
so each grant can overshoot by the call that crossed its line; what a helper spends past its
allotment is charged to the lead's grant too, so the first grant's spending is the whole case's
and the lead stops at its next step. A case can therefore overshoot by at most one call per
grant in its tree. A call the SDK bills but then raises on isn't charged. A helper meant to see no personal
data can't be handed a lead's task; the crew (slice 4) has to give such helpers code-built input.
