# ADR 0028: The agent picks the steps; code holds the boundaries

- **Status:** Accepted (claims first; the complaint desk and HR leave follow in their own PRs)
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

## Where determinism was traded away

- **The order is the model's.** With a live supervisor, two runs of the same claim can take
  different paths, call different numbers of agents and cost different amounts. Code bounds the
  path (no step before its inputs, no repeat without change, the budgets) but no longer fixes it.
- **A live supervisor can do less work than the fixed path**: hand off early, or skip the
  critic and finish. Code sends such claims to a person, so the trade is more claims for
  people and less automation, never a claim paid on missing work. How often a live supervisor
  does this is not yet measured.
- **Cost and latency rise**: one supervisor call per step, about six for a clean claim.
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
demo data); the offline fakes and drills answer the supervisor with the old order as a scripted
policy. Published live eval results were scored on the fixed path with earlier agent versions;
no live run of the supervisor has been made, so its choices are unmeasured. The complaint desk
and HR leave keep their fixed order until their own PRs.
