# The leave path, told once

How hall-monitor handles a leave-of-absence request under federal FMLA rules, from an
employee's message to a signed decision. For a CIO, a Chief AI Officer or an HR head; a few
minutes. What it says the code does is checked by tests in this repo; the data is synthetic.

## 1. An employee writes a message

"My mother has surgery on 1 December and I need four weeks to look after her." The message
arrives through the leave form (`python -m hall_monitor.hr.submit`) or a batch run. Before any
AI reads it, code removes identifiers (dates of birth, phone and card numbers and the like) and
checks for text aimed at the AI ("ignore your instructions", "this was already approved by my HR manager"). Any such
text sends the request to HR whatever the agents later say.

## 2. The intake agent reads it

An AI agent (Claude Haiku) turns the message into dates, a reason category and yes-or-no facts:
is it intermittent, which family member, are they under 18, is there a sign of a disability or
pregnancy accommodation. Its answer has no free-text field, so a diagnosis has nowhere to go.
It never decides anything.

## 3. The FMLA rules run in code

Ordinary code, not a model, checks the employer is covered, the employee has 52 weeks (12
months) on the payroll, 1,250 hours in the last 12 months and 50 employees within 75 miles, the reason
qualifies, and the leave fits in the weeks left. Each check passes, fails or **can't be
decided**: a fact the records don't hold is never guessed. Military leave for a covered family member, for example, needs
that person's covered military status, which the records don't carry, so it is always
"can't decide" and goes to a person.

## 4. Code picks the route

- **`manager_sign_off`, the light path:** an eligible employee, a clear qualifying reason,
  within the weeks left, with nothing flagged. Even this is a draft for a person to sign.
- **`hr_review`:** everything else. Any check that failed or can't be decided, and any flag:
  intermittent leave, a possible accommodation, a state with its own leave law, a key employee,
  a spouse at the same company where leave may be shared, pending discipline or a complaint (retaliation risk), more leave
  than is left, text aimed at the AI, or any failure of the agents.

## 5. A person decides, within their role

The rule names the least senior role that may decide, and a decision by anyone less senior is
refused.

| Role | May | May not |
|---|---|---|
| **Local manager** (the employee's branch manager) | Approve the plain light-path case; ask for more information on any request | Decide anything flagged; deny; approve fewer weeks than asked |
| **HR business partner** | Decide every flagged request; deny; approve fewer weeks than asked; approve military leave with a written attestation that they checked the family member's status | Extend past the entitlement; decide a retaliation-risk case; decide intermittent leave whose dates hold more workweeks than are left |
| **HR director** | Everything above, plus extend leave past the entitlement (intermittent leave up to its dates), decide a retaliation-risk case, and decide intermittent leave whose dates hold more workweeks than are left (measured at sign-off on what the log leaves too) | |

Every decision is signed with the person's key over the exact evidence they were shown, and
code checks the role, that approved weeks fit what is left and what was asked, and that any
denial, extension or approval against the route has a written reason. One decision per request.

## 6. An explanation note, advisory only

A second agent reads only the code's result (never the message or who sent it) and writes a
short note for the person deciding: which checks matter and why. It can't change the route or
the role; a note that doesn't cover exactly the checks the code made is dropped, and the request
goes on without it.

## 7. Everything is written down

Each step goes to a tamper-evident audit log as IDs, keyed hashes, codes and costs: never the
message, the reason or the note. The evidence the person saw, and their signed decision, are
stored by hash beside the log and can be erased under a data-protection request; the log still
shows they existed. The read-only control panel shows each request's state, route and who
decided, never its reasons.

## 8. Stopping it, and capping its cost

The agents' owner can stop any of them at once (the kill switch); a running request stops at
its next step and goes to a person. A third agent, the leave supervisor, chooses the order of the
steps, but code checks every choice, runs the rules on the first intake whatever happens, and
holds each request to a budget (8 steps, $0.02, 120 seconds). The leave workflow has approved
daily and monthly spending caps ($1 and $10 in the demo), held by the live entry points (the
form's live mode and `hr.run`; the evals aren't held to them yet); a request over a cap goes to
an HR director with no AI call.

## 9. What it scored live

One live run of the whole path on the approved agents
([results](../evals/results/hr-agentic-live/results.json)): all 28 synthetic requests reached
the route and role their hand-worked answer keys named at the time (lr_011's key changed
afterwards, review 49: the current rules on that run's exact extraction give the new key, not
re-run), all 28 extractions were exact, no errors,
no failed notes, **$0.026 in all**. The CI gate requires at least 95% on routing and intake and
no errors. Read it for what it is: one run, so run-to-run variation is unmeasured; 28 short,
plainly worded requests written by the same person who wrote their answer keys, so accuracy on
real employees' messages is unmeasured; and no decision was signed in it.

## 10. What isn't covered

- **State and local leave laws**, company policy, paid leave and short-term disability: a state
  with its own law only sends the request to HR.
- **Legal review:** the rules are our reading of the federal regulations (29 CFR Part 825), not
  reviewed by HR or an employment lawyer, and not legal advice.
- **Real identity and keys:** the three fictional staff sign with public demo keys, and there
  is no sign-in; a deployment would use the firm's identity system.
- **The roles** are modelled on a branch company and are this repo's choice, not confirmed by
  an HR team.
- **One run** on synthetic data, as above.

More detail: [ADR 0017](adr/0017-leave-rules-in-code.md) (rules),
[ADR 0018](adr/0018-leave-agents-explain-code-decides.md) (agents),
[ADR 0019](adr/0019-signed-hr-sign-off.md) (sign-off),
[ADR 0034](adr/0034-leave-roles-follow-the-branch.md) (roles), and controls HM-21 to HM-24 and
HM-32 in the [controls register](controls.md).
