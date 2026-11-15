# ADR 0032: Partner claims: the rules route, a person decides, nothing pays

- **Status:** Accepted (slice 1: the rules in code)
- **Control:** [HM-36](../controls.md#hm-36)

## Context

A fourth worked example: marketing development fund (MDF) and co-op reimbursement claims at a
fictional hardware maker ([design](../partner-claims-design.md)). Public program terms
(Cisco, Microsoft, Dell) agree on the lifecycle, and the published abuse patterns (duplicate and
inflated invoices, backdating, split claims, payment redirection) are mostly catchable by
deterministic checks. What isn't (whether a screenshot is recycled, whether a vague line hides an
ineligible cost) needs judgment. The repo's pattern, set by the claims, HR and complaint
examples, is rules first, agents second.

## Decision

- **The rules come first, in code, with no model** (`hall_monitor/partners/rules.py`). Program
  terms are data in `data/synthetic/partners/program.yaml`; every outcome is a fixed reason code
  with a fixed text.
- **Every claim goes to a person.** The route is compliance review, hold, review or expedited
  sign-off; there is no automatic approval and no payment. Green means a person signs quickly
  and the claim is eligible for post-payment sampling, the pattern public fast-lane programs
  use (pay proven partners first, sample afterwards; HP Amplify Fast Lane, November 2023, is
  one published example).
- **The least senior role is the higher of the dollar ladder for the amount claimed and what a
  reason demands.** The amount claimed, not what survives the rules, because whoever releases a
  held part needs authority for the whole. The
  ladder ($5,000 / $25,000) is an assumption: no vendor publishes its thresholds. A split-claim
  pattern needs the next role up; an activity before approval, a payee mismatch or text aimed
  at the AI needs the finance controller; anything compliance-flagged goes to a compliance
  officer whatever the amount.
- **Money is decimal, and the rules only report it:** what may be reimbursed and what is held.
  Nothing in this code can move money or change a payee.
- **Answer keys are hand-written** from the program terms, not from the code, with boundary
  cases on both sides of every numeric rule.

## Alternatives considered

- **Let a model triage the claim end to end.** Rejected: the abuse that matters most (duplicates,
  payee changes, deadline and threshold games) is deterministic, and a model can be talked out
  of it by text in the documents.
- **Auto-approve green claims.** Rejected for now: no public vendor lets an agent approve or pay
  these claims, and the post-payment sampling that makes fast lanes safe needs a person to own
  it.
- **Reject instead of hold on a rate variance.** Rejected: practitioners advise holding the
  variance and paying the rest, so honest partners aren't punished for a pricing question.

## Consequences

- The example starts as rules with no agent, so nothing is measured on a model yet.
- The approval ladder and caps are demo settings; a real program would set its own.
- Slices 2 to 6 (agents, pipeline, evals, panel, live run) follow the
  [new-worked-example skill](../../.claude/skills/new-worked-example/SKILL.md).
