# Design: a complaint investigation crew, with passes that only narrow

- **Status:** Proposed (Hari, 2026-11-11: "lets start with 1"). Written before the code, on the
  branch, so the reasoning is recorded. Where the build differs, the ADRs and the controls
  register win and this page is updated in the same pull request.
- **Controls:** HM-29 (delegation narrows, slice 1). Later slices take the next numbers as they
  land.
- **Builds on:** the complaint desk ([design](complaint-desk-design.md), HM-25 to HM-28).

## Why this example

Every agent in the repo so far is called by code, does one job and hands its answer back to
code. Real deployments are moving to agents that hand work to other agents: a lead agent plans,
then asks helpers to research, analyse and write. That adds a risk none of the existing controls
cover. A helper can end up with more reach than the agent that asked it for help (a confused
deputy), a chain of handoffs can loop or grow without limit, and once work has passed through
three agents nobody can say who allowed what.

The crew takes a complaint the desk has already sent to specialist review and prepares a case
file for the specialist. It is the same simulated company complaint desk: nothing it writes is
sent to anyone (HM-25 applies unchanged), and a person still owns every complaint.

## The crew

| Agent | Job | Tools | Data it may see |
|---|---|---|---|
| `crew_lead` | Reads the triaged complaint, decides which helpers to ask and what to ask them, and assembles their answers | none of its own | PII, financial |
| `rules_researcher` | Says which consumer finance rules the complaint touches (for example Regulation E for an unauthorised transfer, Regulation Z for a billing dispute, the FDCPA for debt collection) and cites the section | `reg_lookup` | PII, financial: a task the lead writes may carry them (slice 2) |
| `pattern_analyst` | Says whether the desk has seen a run of complaints like this one recently | `complaint_patterns` | financial (counts only) |
| `case_writer` | Writes the case file for the specialist from the helpers' answers | none | PII, financial |

Code still decides. Routing, urgency, the queue and the reasons come from HM-27 and are never
changed by the crew. The case file is advice for a person, stored by hash, never sent.

## The new control: passes only narrow when work is handed down (HM-29)

A **grant** is a pass for one case. It names the case scope, the agent holding it, the chain of
agents it came through, the tools and data classes it allows, and a cost allotment.

1. **Only code issues the first grant.** The pipeline issues the lead's grant for one case, and
   the grant can't hold more than the lead's manifest allows.
2. **A handoff must be declared in the hall pass.** A manifest lists the agents it may delegate
   to (`may_delegate_to`). The lead may delegate to its three helpers; the helpers may delegate
   to no one. A handoff not in the manifest is refused.
3. **A child grant is never wider than its parent.** Its tools and data classes must be inside
   both the parent's grant and the helper's own manifest. Asking for more is refused and logged,
   not quietly trimmed, so an attempt to widen a pass is visible.
4. **No loops, a fixed depth.** An agent already in the chain can't receive a grant again, and a
   chain can be at most two handoffs below the pipeline.
5. **Cost allotments only shrink.** What a grant gives its children can't add up to more than
   its own allotment.
6. **Every use checks the whole chain.** Each tool call under a grant checks every agent in its
   chain against the registry (active, approved, not stopped). Stopping the lead stops every
   helper working for it, at once, mid-run.
7. **The case's grant book is the authority.** A grant counts only if this case's book issued it,
   so a grant object built anywhere else is refused. Closing the book at the end of the run ends
   every grant in it.
8. **Agents in a crew can't work without a grant.** The tool gateway refuses a tool call from any
   agent that can delegate or be delegated to unless it comes with a grant from the book.
9. **Every grant and refusal is in the audit log.** `delegation_granted` and
   `delegation_refused` hold IDs, the chain, tool names, data classes and allotments; no
   complaint content.

Helpers' answers flow back to the lead as untrusted data, fenced like any other model output. A
helper has no way to ask the lead to act for it: it holds no delegation rights and its answer is
data, not instructions.

## How data is kept small

- A task one agent writes for another is taken to carry every data class the writer holds
  (slice 2), so a helper is handed work only if its hall pass allows all of them. A helper meant
  to see no personal data (for example a rules researcher given only the labels) has to get its
  input from code, not from a task the lead wrote; slice 4 decides which helpers work that way.
- The pattern tool returns counts by product and issue over time for the whole desk, never other
  complaints' records, and suppresses any count under five. It doesn't break counts down by
  state or ZIP region: HM-28's counterfactual check keeps location out of what agents see, and
  the crew keeps that rule.
- The rules index is a short, hand-written list of public regulation sections with plain-language
  summaries and citations to the eCFR, committed to the repo. No network call is made to look up
  a rule.

## Slices

1. **Delegation in code (HM-29).** `hall_monitor/delegation.py` (grants and the grant book), the
   `may_delegate_to` manifest field, the gateway's grant check, two audit events, this design and
   ADR 0025. No new agents, no model calls. Test-first.
2. **Agents act within their grant (HM-32).** The one agent call path (`run_agent`) runs a crew
   agent only under a grant: every round checks the grant's chain, every model call is charged
   to its allotment and a run over it stops, and its tools are the grant's. A lead is offered a
   `hand_off` tool (`hall_monitor/handoff.py`) that starts a helper's governed run under a child
   grant, with an allotment set by code, and returns its answer as data. ADR 0028. Still no crew
   agents and no model calls: tested with scripted clients.
3. **The read-only tools.** `reg_lookup` over a committed rules index and `complaint_patterns`
   over the desk's store, behind `ToolGateway`, in the read-only catalogue.
4. **The crew.** Four manifests, prompts and approvals; the crew pipeline with a scripted stand-in
   and a fake client; the code's minimum plan (a must-route complaint always gets the rules
   researcher); failures and stops end with the complaint still in specialist review and the case
   file marked incomplete. Then the first live run, under a hard budget.
5. **Evals and the panel.** Case files scored against a hand-written key of which rules each
   label touches, against a code-only baseline; a panel page showing each case's grant tree,
   refusals and cost.

## What this doesn't do

- Agents still run in one process. Grants are allow-lists checked in code, not isolation
  (FINOS MI-22 stays not covered).
- Spending is charged after each model call, so a run can overshoot its allotment by at most
  the call that crossed it (slice 2).
- The grant book lives in memory for one run. A crash loses it, and the case goes to a person
  with no case file, as any failure does.
