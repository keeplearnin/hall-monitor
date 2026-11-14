# Threat model: the agentic loop

STRIDE over the three supervised workflows (claims, the complaint desk, HR leave), where a
supervisor agent chooses the steps and code holds the boundaries
([ADR 0028](adr/0028-agent-picks-the-steps-code-holds-the-boundaries.md), HM-32). Each row names
the threat, what stops it in code, the test that shows it, and what is left. "Left" is a real
residual, not a to-do list; the open ones are also in the register's gaps
([`docs/controls.md`](controls.md)) and the [readiness checklist](readiness-checklist.md).

## What is in scope

- **Assets:** the routing decision (auto for claims, the queue and urgency for complaints, the
  route and role for leave), the audit log, the evidence packs people sign against, the cost of
  model calls, personal and health data in claims and leave requests, and the no-send guarantee.
- **Trust boundaries:** untrusted text (claims, complaints, leave messages, tool results, agent
  outputs) → specialist agents → code → supervisor; the supervisor's answer → code; code → the
  model API (claims and HR through the SDK's own client, the complaint desk through the
  allow-listed transport, which also permits read-only GETs to the CFPB complaint API); people → the stop list, approvals and signed decisions.
- **Attackers:** a claimant, consumer or employee writing the input; a model that has been
  steered or is simply wrong; an insider with write access to the repo or the machine; an
  outage. Out of scope: a compromised model provider, the host OS, and network attackers between
  this process and the API (TLS is the SDK's).

## The supervisor as the new attack surface

The supervisor is the only new component that decides anything, so it gets the strongest
assumption: **treat it as hostile**. Its answer is a closed enum with a reason code and no route,
queue, urgency or role field (`extra="forbid"`); it has no tools; it sees a view code builds from
typed fields (codes, counts, amounts, booleans), never text from the case. Every red-team test
in `tests/test_agentic.py`, `tests/test_desk_agentic.py` and `tests/test_hr_agentic.py` plays a
hostile or broken supervisor, and the offline eval runs 40 scripted policies (hostile, failing,
noisy) against the fixed paths ([results](../evals/results/agentic-scripted/report.md)).

## STRIDE

| | Threat | What stops it (code) | Shown by | Left |
|---|---|---|---|---|
| **S** | A tool result, claim or complaint pretends to be an instruction from the supervisor or the system | The supervisor never sees text from the case; specialists get it fenced and escaped (`agents/base.py` `escape`, `render_claim`); injection detection flags it and routes to a person (HM-09/10) | `test_claim_text_tool_results_and_agent_reasoning_never_reach_the_supervisor`, `test_the_supervisor_has_no_tools_and_sees_no_complaint_text`, `test_the_supervisor_has_no_tools_and_never_sees_the_message` | Injections the detector misses still reach specialists (HM-30 found encodings it misses); their answers are then bounded by routing |
| **S** | An agent runs under another agent's identity or an unapproved prompt | Hall pass per call: manifest digest and prompt-template hash must match the approvals (HM-01); the supervisor's pass is checked on every turn | `tests/test_registry.py`, `test_the_approved_prompt_covers_the_untrusted_claim_notice` | One process, one key holder: no per-agent credentials (FINOS MI-22 not covered) |
| **T** | The supervisor tries to choose auto, a queue or a role | No such field; finishing is a request; code routes (`route_claim`, `route_triages`, `route_leave`) | `test_the_supervisor_s_answer_has_no_route_field`, `…_no_queue_or_urgency_field`, `…_no_route_or_role_field` | None in code; a person can still mis-decide at the approval gate |
| **T** | The supervisor skips a required step, or finishes on stale work | Required-evidence check: auto only if every step ran on the latest inputs (claims); unread complaints at high urgency; requests routed before the rules go to an HR director | `test_finishing_before_a_required_step_ran_goes_to_a_person`, `test_skipping_the_review_after_a_revision_goes_to_a_person`, `test_a_complaint_no_agent_read_goes_at_high_urgency`, `test_a_request_the_rules_never_saw_goes_to_the_most_senior_role` | More cases reach people (cost, not risk) |
| **T** | The supervisor revises only part of what the critic raised, hoping a later clean review hides it | Revise must send back exactly the raised stages | `test_revising_only_some_of_the_stages_the_critic_raised_is_refused`; eval `raising_critic/revise_part` (16 unsafe autos with the check removed, 0 with it) | — |
| **T** | The supervisor re-runs an extraction to shop for a better answer (HR) | The rules run on the first intake; a re-check is allowed once, before the rules, and disagreement adds HR review | `test_the_rules_run_on_the_first_intake_and_a_disagreeing_recheck_adds_review`, `test_a_recheck_after_the_rules_ran_is_refused` | Re-preparing a request after a killed process draws a new intake (ADR 0028; needs process access; both attempts logged) |
| **T** | A specialist returns the wrong type, a partial answer, or a refusal | Typed output validated; only `end_turn` counts; failure stops the case for a person (HM-08, ADR 0007) | `test_a_specialist_answering_with_the_wrong_type_stops_the_claim` and desk/HR equivalents | — |
| **T** | Someone edits the audit log, a checkpoint, an evidence pack or the stop list | Hash-chained log with anchor verify (HM-06); packs stored by hash and checked before a decision, once-only final decisions (HM-05); stops append-only in CI | `tests/test_audit.py`, `tests/test_workflow.py`, `test_a_restored_checkpoints_file_cant_decide_a_claim_again` | Anchoring is manual (`audit head`); SQLite on one machine; a root user can rewrite the whole file and the anchor if both are local |
| **R** | Nobody can show which steps the agent chose, or why | Every choice logged as `supervisor_step`, including a chosen step a stop refused (action, reason code, stages, outcome, refusal, agents, cost, time); the path on the routed or prepared entry and in HR's evidence pack; shown in the panel | `test_every_choice_is_logged_as_codes_counts_and_amounts` and desk/HR equivalents | Reason codes are the model's own claim about why; they are logged as choices, not verified |
| **R** | A decision is disowned | Signed decisions (Ed25519) bound to the evidence hash (HM-05, HM-23) | `tests/test_hr_signoff.py`, `tests/test_workflow.py` | Demo keys in the repo for the demo only; key custody is out of scope |
| **I** | Personal or free text leaks into the log or traces | Log holds IDs, keyed hashes, codes and costs; supervisors' views and steps have no text fields; traces never capture inputs (HM-11) | `test_traces_never_capture_inputs_or_outputs`, `test_each_step_is_logged_with_nothing_the_employee_or_a_model_wrote` | Redaction is pattern-based (HM-10); identifiers it doesn't know reach specialists |
| **I** | The supervisor receives more data than it needs | Its manifest's data classes are checked at run time; its view carries only codes and amounts (health for the leave-reason and claim-type codes) | `tests/test_privacy_security.py`, the view models in `agents/supervisor.py`, `complaints/supervisor.py`, `hr/supervisor.py` | Codes still reveal that a claim or leave is medical; declared as health data |
| **I** | Something is sent out (a reply, an email, an upload) | The complaint desk's calls go through `egress.py`'s allow-list; a static check fails the build on any other direct HTTP use; no manifest may grant a sending tool (HM-25) | `tests/test_no_send.py`, CI "No sending capability" | Claims and HR call the model through the SDK's own client, not the allow-listed transport; the model API receives redacted text (by design) |
| **D** | The supervisor loops, repeats steps or burns money | Refusals (3 → a person), repeats refused, budgets per case: steps before every turn, cost and time before every model request, each tool round included; every call bounded at 60 s per attempt, two attempts (`default_client`) | `test_a_supervisor_repeating_a_step_is_refused_and_the_loop_stops_for_a_person`, `test_running_out_of_*`, `test_a_revision_stops_between_agents_when_the_budget_runs_out`, `test_the_default_model_client_bounds_each_call`, `test_the_time_budget_holds_inside_a_specialist_s_tool_loop`, `test_the_cost_budget_counts_the_rounds_a_specialist_has_already_sent` | Overshoot is the one request in flight: about two minutes and its cost; daily and monthly caps per workflow (row below, live entry points only); no per-tenant cap |
| **D** | Many cases, or a looping workflow, run up spend across a day or a month (denial of wallet) | Approved daily and monthly caps per workflow in `registry/budgets.yaml`; each live case reserves its budget in a shared ledger before any model call, and a case over a cap goes to a person with no model call; an unapproved budgets file or an unreachable ledger refuses the case the same way (HM-34, ADR 0030) | `test_the_daily_cap_refuses_a_case_that_would_pass_it`, `test_two_processes_can_t_both_take_the_last_of_a_cap`, `test_a_claim_over_the_cap_goes_to_a_person_with_no_model_call` and desk/HR equivalents, `test_a_broken_ledger_sends_a_claim_to_a_person_with_no_model_call` | Caps are per workflow, not per agent; the evals' live runs aren't held to them; one machine's ledger; an open reservation from a crash counts until the day (and, for the monthly cap, the month) rolls over |
| **T** | Someone raises a cap or lowers a price to let more spend through | Budgets and prices are approved by digest like a hall pass; an unapproved edit stops every case and unprices every model | `test_an_unreviewed_change_to_the_budgets_is_refused`, `test_a_price_change_needs_approval_too`, `test_call_cost_prices_nothing_while_the_budgets_are_unapproved` | As for approvals: branch protection is off, so one person can approve and merge |
| **D** | An unpriced model makes the cost cap read zero | The loop stops when a cost can't be priced | `test_an_unknown_cost_stops_the_desk_loop`, `test_a_model_without_a_price_stops_the_request` | — |
| **D** | An outage or a broken supervisor leaves cases with no one | Every failure routes to a person; a broken leave supervisor still files the request for HR; interrupted complaints are routed by the next run | `test_a_supervisor_outage_before_intake_still_reaches_hr`, `test_an_interrupted_run_before_triage_goes_at_high_urgency`, failure drills (HM-31) | A supervisor outage files leave requests with nothing extracted (deliberate cost); interrupted leave and claims runs wait for a re-run |
| **E** | An agent reaches a tool or data beyond its manifest | Gateway allow-list and data-class check on every call; claims lookups limited to the claim's policy (HM-03); supervisors have no tools | `tests/test_tools.py`, `tests/test_delegation.py` | Allow-lists in one process, not isolation |
| **E** | A stopped agent keeps working | Stops read on every call; every supervisor turn and every chosen step re-checks every agent the case has used | `test_stopping_the_supervisor_mid_run_stops_the_claim_at_the_next_step`, `test_a_stop_on_a_specialist_that_already_answered_halts_the_claim_at_the_next_step`; [kill-switch drill](../evals/results/kill-switch-drill/report.md) | A call in flight finishes (HM-19); a stop on an agent the case hasn't used yet takes effect when it is next needed |
| **E** | A change to a prompt or manifest ships without review | Approvals pin hashes; the agent is refused at run time until re-approved; CI fails | `tests/test_registry.py`, CI | Branch protection is off, so CODEOWNERS isn't enforced and one person can merge (Hari's setting) |
| **E** | Separation of duties: the same person approves a change and the decision | Signed decisions need an adjuster or HR role with the authority the rules name | `tests/test_approval_rules.py`, `tests/test_hr_signoff.py` | Repo approvals and decisions aren't separated by tooling; no sign-in on the panel (milestone 15) |

## What would change this model

- **A live supervisor run.** Every row above is shown with scripted supervisors. A live model
  can't do more than a hostile scripted one (the boundaries are in code), but how often it
  hands off, re-checks or fails is unmeasured, and so is its cost.
- **Multiple processes or machines.** The stop list, the audit log and the once-only checks
  assume one shared filesystem; milestone 15's shared store changes the concurrency rows.
- **Tools for a supervisor.** It has none. If it ever gets one, its specialist calls should go
  under the delegation grant book (HM-29), and this model needs a new **E** row.
