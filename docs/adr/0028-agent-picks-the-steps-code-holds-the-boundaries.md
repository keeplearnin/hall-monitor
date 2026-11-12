# ADR 0028: The agent picks the steps; code holds the boundaries

- **Status:** Accepted (claims, the complaint desk and HR leave)
- **Control:** [HM-32](../controls.md#hm-32)

## Context

Until now code fixed the order of every workflow: the claims pipeline ran intake, the policy
check, coverage, risk, the critic loop and the recommendation in that order every time
(`pipeline.py`), and each agent ran its own bounded tool loop (`agents/base.py`,
`MAX_TOOL_ROUNDS`). The agents were tools in a script. Real deployments are moving to agents
that decide the next step themselves: re-check, ask for review, stop and hand off. That is what
"agentic" means here, and it moves a decision that used to be code's into a model, so the
controls have to hold without relying on the order.

`delegation.py` (HM-29) already exists, but it answers a different question: what reach an
agent may pass to another agent it hands work to. The supervisor holds no tools and passes no
reach; code calls each specialist under the specialist's own hall pass. So the grant book is
not used here. If a supervisor is ever given tools, its specialist calls should go under grants.

## Decision

- **A supervising agent per workflow** (claims: `claims_supervisor`, Haiku, its own manifest
  and approved prompt) chooses one action at a time from a closed list: run intake, coverage,
  risk, the critic or the recommendation, revise the stages the critic raised, finish, or hand
  off. Its answer is a typed `SupervisorDecision` with a reason code. It has no tools and no
  route field.
- **It sees codes and amounts, never text.** Code builds the supervisor's view from typed
  fields: which steps ran, whether each result is current or out of date, decision labels,
  amounts, flag codes, the stages the critic raised, its last step's outcome and the budget
  left. No claim text, tool result, agent reasoning or critic wording reaches it, so text
  planted in any of them can't steer it. Tool results stay untrusted input to the specialists,
  as before.
- **Code checks every choice before it runs**: a step whose inputs aren't ready, or that would
  repeat on unchanged inputs, a revision that doesn't send back exactly the stages the critic
  raised (all of them, as the fixed path does), or one past `MAX_REVISIONS`, is refused; nothing runs, the refusal is logged and shown to the supervisor,
  and after three refusals the claim stops for a person.
- **Budgets per claim:** steps (16), checked before every turn, finish included; cost
  (0.25 USD) and wall-clock time (300 s), checked before every model call the loop makes, the
  supervisor's and each specialist's, each agent of a revision included. Running out stops the
  claim for a person, and the overshoot is at most one agent's run with its tool loop.
- **The kill switch at every step.** The supervisor's hall pass is checked when it is asked
  and again before the step it chose runs; each specialist checks its own on every model and
  tool call, and the routing step checks every agent the result rests on.
- **Final routing stays in code.** Finishing is a request. Code checks the required evidence
  (intake with a policy found, coverage, risk, the critic when it's on, and the
  recommendation, each computed on the latest inputs) and then runs the same `route_claim`, with
  its amount trace, as the fixed path. Anything missing or out of date goes to a person; a
  hand-off always does.
- **Code keeps what was code's**: the policy check and the one correction from the claim text
  after intake, the decision label from the amount, rerunning coverage and risk after an intake
  revision, and the record of every revision (a critic-driven change to a decision field still
  sends the claim to a person).
- **Every choice is logged as it happens** (`supervisor_step`): action, reason code, stages,
  outcome, refusal code, agents run, cost and time so far. No free text. The panel's case trace
  shows these entries.
- **The fixed path is kept** (`triage(..., agentic=False)`) as the comparison, and an offline
  eval runs both over every synthetic claim with scripted supervisor policies, hostile ones
  included, and with a critic that raises issues and then changes its mind
  (`evals/agentic_compare.py`, gated: no policy may get a claim through that the fixed path sent
  to a person). Review 22 found that a revision of only some raised stages did exactly that;
  it is refused now, and the eval's revision scenario catches it if the refusal is removed.

## The complaint desk

The same pattern on the desk, where every complaint already goes to a person and code chooses
the queue (HM-27). The `complaint_supervisor` (Haiku, no tools, no data classes: it sees codes
only) chooses among four actions: run the triage agent, ask for one independent re-check, finish,
or hand off to specialist review. Code holds the rest:

- **It can only add review.** Every triage's answer counts in routing (each can add reasons and
  raise urgency, none can remove either); two triages that disagree on category, urgency or
  concerns add `triage_disagreed`; a hand-off adds `supervisor_handed_off`; finishing before any
  triage adds `triage_skipped`. On the agentic path a complaint no agent read goes at high
  urgency, however the loop ended (finish, hand-off, failure, stop or budget): the offline eval
  found that ending before triage kept the code's floor where the fixed order's triage could
  have raised it (77 of 500 complaints), and review 23 found the same for failures. An
  interrupted triage (a crash or a killed run) goes at high urgency on either path, since no
  answer survived. The fixed path keeps the floor for a triage that failed and was routed.
- **Refusals, budgets and the kill switch** as for claims: a second triage or a third is
  refused, as is a re-check before the first; three refusals, or running out of steps (6), cost
  (0.02 USD) or time (120 s), or a cost that can't be priced, send the complaint to specialist
  review as `supervisor_failed`; a failure of the triage call itself, or a stop on the triage
  agent, is `agent_failed`. Both are counted in the complaint evals and gated at zero. The
  routed entry records the path and whether it was agentic.
- **No reply, no send (HM-25)** is unchanged: the supervisor's manifest has no tools and passes
  the no-send check, and the draft kept is the first triage's, credited to the triage agent.
- **Logged:** each choice as `supervisor_step` in the complaint's scope, and the path on the
  routed entry, shown in the desk page's complaint table.

## HR leave

The `leave_supervisor` (Haiku, no tools, health data only: it sees the leave-reason code)
chooses among six actions: run the intake agent, ask for one independent re-check of the
intake, run the rules (code), run the explanation agent, finish, or hand off to HR review.
Every request is still for a person to sign, and code still names the route and the role.

- **No shopping for an answer.** The rules always run on the first intake. A re-check is
  allowed only before the rules run; if the two intakes disagree, `intake_disagreed` sends the
  request to HR review. A re-check after the rules ran is refused.
- **It can only add review.** A hand-off adds `supervisor_handed_off` and keeps the code's
  reasons and role. A request routed before the rules ran (a finish or hand-off before them, a
  refusal loop, a failure) gets `assessment_skipped`, whose role is HR director: without the
  rules nobody knows which role they would name. The offline eval found that without this, 9 of
  28 requests would have gone to a leave specialist where the rules name an HR manager or
  director. The fixed path still gives a leave specialist a request whose intake failed, as
  before.
- **The note stays advisory.** Finishing without it keeps the route and says why; a failed note
  is recorded, never a stop.
- **Refusals, budgets (8 steps, 0.02 USD, 120 s, an unpriced cost) and the kill switch** send
  the request to HR review as `supervisor_failed`; an intake failure is `pipeline_stopped`.
  Only an intake-call failure before any intake answered leaves the request with no evidence
  pack, to be prepared again, as on the fixed path. Every ending the supervisor chose and every
  failure of the supervisor stores the pack, so a hand-off or a broken supervisor never keeps a
  request from HR (review 24 found a hand-off before intake left the request unsignable).
- **The note's agent is advisory at finish too:** a stop on the explanation agent after it
  answered drops the note and keeps the route.
- **The person signing sees how it was prepared:** the evidence pack holds the mode, the
  supervisor's path and, when two intakes disagreed, the re-check's intake to compare.
- **Interrupted runs** (a killed process) leave the request "being prepared", with no pack, as
  on the fixed path; preparing it again draws a new intake. Only someone who can kill the
  process can do that, and the log shows both attempts.
- **Logged:** each choice as `supervisor_step` in the request's scope, and the mode and path on
  `leave_prepared`, shown in the panel's case trace.

## Where determinism was traded away

- **The order is the model's.** With a live supervisor, two runs of the same claim can take
  different paths, call different numbers of agents and cost different amounts. Code bounds the
  path (no step before its inputs, no repeat without change, the budgets) but no longer fixes it.
- **A live supervisor can do less work than the fixed path**: hand off early, or skip the
  critic and finish. Code sends such claims to a person, so the trade is more claims for
  people and less automation, never a claim paid on missing work. How often a live supervisor
  does this is not yet measured.
- **Cost and latency rise**: one supervisor call per step, about six for a clean claim.
- **On the desk**, a live supervisor may ask for re-checks (more cost, and a disagreement sends
  the complaint to specialist review) or hand off; it can't give a complaint less review than
  the fixed order did, which the offline eval checks on all 500 pinned complaints.
- **On HR leave**, a live supervisor may re-check the intake or hand off; a disagreement or an
  early end sends the request to HR review, at the most senior role if the rules never ran.
- **What stays deterministic** is everything that decides an outcome: preconditions, budgets,
  the required-evidence check, routing and the audit record.

## Alternatives considered

- **Let the supervisor see the claim.** Rejected: it would then read untrusted text while
  choosing steps, and an injection could steer it to skip work. Code already sends skipped work
  to a person, but keeping text out removes the lever.
- **Let the supervisor choose the route, with code vetoing auto.** Rejected: a route field
  invites trusting it; finish-as-request keeps the decision visibly in code.
- **Use the grant book (HM-29) for each specialist call.** Not needed while the supervisor holds
  no tools: specialists already run under their own manifests and the gateway's allow-lists.
- **Keep the fixed order and call it agentic.** Rejected: the specialists' tool loops were
  already bounded agents; what was missing was the model choosing the workflow.

## Consequences

The claims workflow runs agentic by default (the workflow, the CLI, the drills and the panel's
demo data), and so does the complaint desk (its CLI, evals, the red-team arena and the demo
data), and so does HR leave (its CLI, evals, the submission form and the demo data); the
offline fakes, drills, arena and scripted stand-ins answer the supervisors with the
old order as a scripted policy. Published live eval results were scored on the fixed path with earlier agent versions;
no live run of any supervisor has been made, so their choices are unmeasured.
