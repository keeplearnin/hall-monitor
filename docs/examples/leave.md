# Leave of absence (FMLA)

> **Status:** rules, agents, signed sign-off, panel cases and a submission form are built. **One
> live run on the approved agents**, of 28 short synthetic requests (see [Results](#results)).
> The rules haven't been reviewed by HR or a lawyer.

## Why this domain

The offline demonstration for a CIO, a CISO and an auditor runs this example beside claims and the complaint desk: [the briefing](../briefing.md).

The same pattern on an HR workflow: an employee asks for leave, and someone has to decide whether federal FMLA rules make them eligible and how much leave is left. A wrong denial harms an employee, and the request touches health information an AI must not collect. The whole path, told once for a CIO or an HR head: [The leave path, told once](../leave-path.md).

## What the agents do

Three agents, through the same registry, approvals, kill switch, data-class check and audit log as the claims agents:

| Agent | What it does |
|---|---|
| **Leave intake** | Reads the employee's message (identifiers redacted, fenced as untrusted) and returns the dates, a reason category and yes-or-no facts. Its answer has no free-text field, so a diagnosis has nowhere to go. |
| **Leave supervisor** | Chooses the next step (run intake, one independent re-check, the rules in code, the explanation, finish, or hand off to HR review) from codes only, with no tools. Code checks every choice, runs the rules on the first intake whatever happens, and holds each request to a budget (8 steps, $0.02, 120 seconds); it never chooses a route or role ([ADR 0028](../adr/0028-agent-picks-the-steps-code-holds-the-boundaries.md)). |
| **Leave explanation** | Reads only the code's result, never the message or the employee and request IDs, and writes a note for the person who decides: the local manager on the light path, HR for anything flagged. Advisory: it can't change the route or the role, and a note that doesn't cover exactly the code's checks is dropped. |

## What code decides

The rules came first, as code with no model (`python -m hall_monitor.hr`):

- **Eligibility:** whether the employer is covered, and whether the employee has 52 weeks on the payroll, 1,250 hours in the last 12 months, and 50 employees within 75 miles.
- **Reason and entitlement:** whether the reason qualifies, and whether the leave fits in the weeks left, counted in workdays, under each of the four leave-year methods.
- **Nothing guessed:** a fact the records don't hold is "can't decide", never a failure.
- **Routing:** a code rule sends every check that didn't pass and every flag to HR review, and names the least senior of the three roles a branch company already has that may decide. The employee's **local manager** prepares every request with them and signs off only a plain request with nothing flagged (`manager_sign_off`), still as a draft for them to sign; an **HR business partner** decides every flagged request, every denial and every partial approval; an **HR director** extends leave past the entitlement and decides a case with a retaliation risk.

The rules run on what intake extracts, so a wrong extraction changes the result; an eval measures that. Text aimed at the AI sends the request to a person whatever the agents say, and any failure of intake or the rules stops it and sends it to a person (a failed explanation only leaves the note out). Each step goes to the audit log as IDs, keyed hashes, codes and costs.

## Who decides

A person decides (`python -m hall_monitor.hr.run` prepares requests into the audit log with the live model; `python -m hall_monitor.hr.review` lists and decides them). A decision counts only if it is signed with the staff member's key over the evidence pack the audit log recorded, and code finds it within their role: at least the role the rule named; approving FMLA leave only for an eligible employee within the weeks left (less the weeks the log shows approved on the same employee's other requests), asked and qualifying; approving fewer weeks than asked, or denying, only by an HR business partner or above; military leave whose family member's covered status the records don't carry only by an HR business partner, with a written attestation that they checked it; extending only by a director, past the entitlement; a reason for any partial or full denial, extension or approval against the route. A local manager never decides a flagged request. Asking for more information leaves the request waiting. One decision per request. The log keeps no reason and no signature, only keyed hashes; the signed decision sits in the erasable evidence store. The control panel shows each leave request's state, route, outcome and audit trace, never its reasons, the role it needs, its checks or weeks (together they could tell what it is about), and counts reasons only across requests.

Requests can also be added from a small form, a separate app so the panel stays read-only (`python -m hall_monitor.hr.submit --demo` or `--live`). It feeds the same pipeline and decides nothing; the submitter sees only the request's ID and state. Demo mode offers the synthetic messages with the scripted model and calls no API; live mode takes free text, calls the Claude API for each submission and stops at a per-run cap. It serves this machine only, and takes a submission only from its own page, so another site can't post one through the browser; with no sign-in, anyone on this machine can still submit.

## Data

28 synthetic requests sit on the boundaries, each with an employee's message and hand-worked answer keys ([`data/synthetic/hr/`](../../data/synthetic/hr/README.md)). The rules are our reading of the regulations, not reviewed by HR or a lawyer; state laws aren't modelled.

## Results

One live run of the agentic leave path has been made on the approved agents: all 28 requests routed as their answer keys say, all 28 extractions exact, no errors, 2.6 cents in all. It is one run of short messages written by the person who wrote their answer keys, so accuracy on real employees' messages is still unmeasured. The table and what it can't show are in the README's [Results](../../README.md#results).

## Run it

```bash
uv run python -m hall_monitor.hr                           # leave rules on 28 synthetic requests; no API calls
uv run python -m hall_monitor.hr.evals --out evals/results/hr-intake  # leave agents, 28 requests, under $0.10
uv run python -m hall_monitor.hr.run --all                 # prepare the 28 requests into the audit log, under $0.10
uv run python -m hall_monitor.hr.review list              # leave requests waiting for HR; no API calls
uv run python -m hall_monitor.hr.submit --demo             # form at http://127.0.0.1:8766: add a synthetic request; no API calls
uv run python -m hall_monitor.hr.submit --live             # the form with free text; about $0.003 a submission, 20 per run
```

## Where it lives

- **Code:** `hall_monitor/hr/`.
- **Design and decisions:** [the leave path, told once](../leave-path.md); ADRs [0017](../adr/0017-leave-rules-in-code.md), [0018](../adr/0018-leave-agents-explain-code-decides.md), [0019](../adr/0019-signed-hr-sign-off.md), [0020](../adr/0020-leave-submission-form.md) and [0034](../adr/0034-leave-roles-follow-the-branch.md).
