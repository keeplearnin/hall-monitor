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
| `rules_researcher` | Says which consumer finance rules the complaint touches (for example Regulation E for an unauthorised transfer, Regulation Z for a billing dispute, the FDCPA for debt collection) and cites the section | `reg_lookup` | none: it gets the labels only |
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

- The rules researcher gets the product, issue and sub-issue only. Its grant carries no data
  classes, so a tool returning personal or financial data would be refused to it even if the
  lead asked.
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
2. **The two read-only tools.** `reg_lookup` over the committed rules index and
   `complaint_patterns` over the desk's store, behind `ToolGateway`, in the read-only catalogue.
3. **The crew.** Four manifests, prompts and approvals; the crew pipeline with a scripted stand-in
   and a fake client; the code's minimum plan (a must-route complaint always gets the rules
   researcher); failures and stops end with the complaint still in specialist review and the case
   file marked incomplete.
4. **Evals and the panel.** Case files scored against a hand-written key of which rules each
   label touches; a panel page showing each case's grant tree, refusals and cost.

## What this doesn't do

- Agents still run in one process. Grants are allow-lists checked in code, not isolation
  (FINOS MI-22 stays not covered).
- In slice 1 a grant's allotment limits what it may give its children; spending against the
  allotment is checked from slice 3, when agents run.
- The grant book lives in memory for one run. A crash loses it, and the case goes to a person
  with no case file, as any failure does.
