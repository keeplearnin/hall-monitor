# Design: failure drills, many agents failing on purpose

- **Status:** Accepted for build (Hari, 2026-11-11, idea 6 of the orchestration ideas: "continue
  working on next two recommendations"). Written before the code, on the branch. Where the build
  differs, the ADR and the controls register win and this page is updated in the same pull
  request.
- **Control:** HM-31 (failure drills, slice 1).

## Why this example

Each control in the repo is tested one failure at a time: one wrong answer, one API error, one
stop, one crash, on one claim. Production doesn't fail like that. Many claims run at once, an
owner stops an agent while some claims are halfway through, the API times out for a few of them,
a process dies after writing to the log, a retry submits the same claim twice, and the batch
runs out of budget. The question a risk officer asks is whether the controls still hold when
those things happen together: does every failure still end with a person, is anything decided
twice, does the audit log still verify, and did the batch stay within its budget?

A drill answers that the way a fire drill does: fail things on purpose, on a plan, and check the
outcome in code.

## What a drill does

`python -m hall_monitor.drills` runs the 36 synthetic claims (twice by default, as separate
cases) through the real claims workflow, several at a time on threads, into its own registry
copy, audit log and checkpoints under `var/drills/`. Every control runs as it does for real: the
registry and its stop list, the tool gateway, the routing rule, the once-only evidence pack and
decision, and the audit log. Only the model is scripted from the answer key, so nothing calls the
API and the run says nothing about how well the agents work.

### Failures injected

A plan fixed by a seed gives a share of the cases one fault each, taking the kinds in turn:

| Fault | What happens | What must follow |
|---|---|---|
| `wrong_answer` | Coverage pays one cent more than was claimed | Routed to a person by the routing rule |
| `wrong_type` | One agent answers with the wrong output type | Triage stops; a person |
| `no_output` | One agent returns nothing | Triage stops; a person |
| `truncated` | One agent's answer is cut off (`max_tokens`) | Triage stops; a person |
| `api_error` | One agent's call fails to connect | Triage stops; a person |
| `timeout` | One agent's call times out | Triage stops; a person |
| `crash` | The process dies after the evidence pack is logged, before the checkpoint is saved | Status `needs_a_person` (the HM-05 window) |
| `duplicate` | The same claim is submitted twice at the same moment | Triaged once, decided at most once |

And for the batch:

- **An owner's stop, mid-batch.** When two thirds of the cases have begun, the risk agent's
  owner stops it, written to the drill's registry copy with `add_stop`. Every case begun after
  the stop must end with a person, with the stop in its logged triage result. Cases already
  running may finish: that is the HM-19 gap (a call in flight finishes). The report counts the
  cases in flight at the stop and how many of them were then decided automatically.
- **A cost cap.** Each case reserves a cost ceiling before it starts and settles what it cost
  when it ends, so cases running at once can't together pass the cap. A case that would pass it
  isn't started; the log records `batch_budget_reached` for it, and it waits for a person.

### Checks, in code

Read from the audit log and the workflow's status, never from the runner's own counts:

1. Every failed, crashed or stopped case ends with a person (waiting for an adjuster, needing a
   person, or not started); none is decided automatically.
2. No case is left in progress with no one.
3. Clean cases that finished before the stop were handled normally: decided automatically
   exactly when the answer key routes them so. Without this, a drill could pass by sending
   everything to a person.
4. No case has more than one evidence pack or final decision.
5. Every injected failure shows in the log: a stopped triage for a model fault, a person's route
   for a wrong answer, the stop in the triage result after the stop, `batch_budget_reached` for
   a case not started, `needs_a_person` for a crash.
6. No case cost more than its ceiling, and the cost the log records for the batch is under the
   cap.
7. The audit log verifies.

The report counts each fault by outcome, says how many cases began after the stop and how many
weren't started, and lists every problem. Exit code 1 if there is any. An unforeseen error in
one case is recorded by its type and flagged; it doesn't end the drill. Three mutation tests
make sure the checks can fail: a routing rule that lets everything through, a stopped claim
routed automatically (refused at the automatic step and left with no one), and a cap that never
refuses.

## Slices

1. **The drill runner and its checks (HM-31).** Claims workflow only. This page and ADR 0027.
2. **More pipelines and the panel.** The complaint desk and the HR leave pipeline under the same
   drill, and a read-only panel page for the latest drill report.
3. **Scheduled drills.** A drill in CI on every pull request (a small one) and a larger one on a
   schedule, with the report published.

## What this doesn't do

- It runs threads in one process against SQLite files, not separate processes or machines. The
  once-only checks were tested across real processes before (HM-05); the drill doesn't repeat
  that.
- The model is scripted, so faults are the ones we wrote. A real model fails in other ways too.
- The cost cap is the drill runner's, not the production pipeline's. It shows how a batch budget
  can be held with reservations; the claims CLI has no batch cap yet.
- A case refused by the cap isn't retried in the same run, even if room appears once running
  cases settle, so a drill can leave part of the cap unspent.
