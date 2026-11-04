# ADR 0007: Any failure stops the claim and sends it to a person

- **Status:** Accepted (milestone 3; refusals added after the first code review)
- **Control:** [HM-08](../controls.md#hm-08)

## Context

Models time out, refuse, return output that doesn't fit the schema, or can't find the policy.
A pipeline that guesses its way past a failure produces decisions nobody can explain.

## Decision

Every failure becomes a stopped result: an API error, a refusal (`AgentRefusedError`), output
that fails validation or isn't the type asked for, a turn that didn't finish (any stop reason
but `end_turn`), a refused hall pass, a policy that can't be found, a tool loop past
`MAX_TOOL_ROUNDS`, or any other unexpected error.
Stopped claims go to a person with the reason (`route_stopped`) and are recorded in the audit
log. The first version crashed on a refusal; a review caught it. A later claims audit found
that errors outside the expected kinds (a tool server error, say) still crashed the run, so
`triage` now catches any exception as a last resort.

## Consequences

- No default value is ever invented to keep a claim moving.
- Catching every exception can hide a programming error as a stopped claim. The stopped reason
  names the exception type, and eval runs count stopped claims, so a bug shows up there.
- Stopped claims add to adjuster workload; the stopped-claim rate is reported with every run.
- Eval runs count API failures as errors, not as correct stops, so an outage can't flatter the
  results (a lesson from a run that ran out of credit).
