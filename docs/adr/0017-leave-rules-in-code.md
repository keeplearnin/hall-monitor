# ADR 0017: Leave eligibility and who decides are code, built before any agent

- **Status:** Accepted (HR example, first slice)
- **Control:** [HM-21](../controls.md#hm-21)

## Context

The claims example shows the controls on one workflow. A second workflow with different
sensitive data (health conditions, family details, pay and hours) and different law tests
whether the controls are built around claims or around agents. The design for HR review chose
leave of absence under federal FMLA rules: the eligibility rules are exact enough to check in
code, a wrong denial harms an employee, and the request touches medical information an AI must
not collect.

## Decision

- **The rules first, the agents later.** This slice is the part a regulator or an employee's
  lawyer would ask about: who is eligible, how much leave is left, and who must decide. It is
  ordinary code (`hall_monitor/hr/`) with no model, so agents added later can explain the result
  but can't change it, as with the claims routing rule ([ADR 0001](0001-routing-decided-in-code.md)).
- **Three answers, not two.** Each check passes, fails, or can't be decided from the records
  (a missing hours week, coverage year, distance or employer record, an unclear reason, an
  adult child whose condition a person must check, earlier caregiver leave for a servicemember
  the records don't name), with the facts it used. A missing fact is never filled in with zero
  or "far": that would show an HR manager a reason to find someone ineligible that the records
  don't support. It goes to a person, as a failed claim does
  ([ADR 0007](0007-failures-stop-the-claim.md)), and the routing rule routes on each check's
  answer, so a check that didn't pass reaches a person whatever its reason code.
- **Leave in workweeks.** Leave already taken and leave asked for are counted in workdays of the
  employee's normal workweek, so a Monday-to-Friday week is one workweek and a single day a fifth
  of one (825.205(b)); service counts every week on the payroll (825.110(b)(3)).
- **Code decides who decides.** The routing rule sends every failure, unknown and flag to HR
  review and names the least senior role that may decide it, from the sign-off table in the
  design for HR review (a Claude Doc outside this repo; the roles for six flags are this code's
  choice). Even a clean request is only a draft for a leave specialist to sign: the design asked
  HR whether any approval may skip a person, and until they answer, none does. The sign-off
  ([ADR 0019](0019-signed-hr-sign-off.md)) enforces the role.
- **Answer keys on the boundaries.** 28 synthetic requests sit on the edges (hired on a
  Saturday against the next day, 1,250 against 1,249 hours, 50 against 49 employees, a site at
  exactly 75 miles, a leave that partly falls outside the rolling year), each worked out by
  hand, and most rules are tested on both sides of their boundary. (When the rules were
  first written, each was broken by hand once to check that a test fails; that check isn't
  automated.)
- **Coarse records.** The records hold no names and no diagnosis; pending discipline is a yes or
  no, and the age of a child being cared for is only under 18 or not. They do hold health
  information, kept as coarse as the rules allow: the reason category of a request and of leave
  already taken, whether an adult child can't care for themselves, and signs of an
  accommodation need; and bonding requests carry the date of the birth or placement, which the
  12-month window needs. A test fixes the list of fields of every record, so adding one is a
  reviewed change.

## Alternatives considered

- **Build the agents first, as the claims example did.** Rejected: there the routing rule was
  hardened after reviews found it trusted the agents' labels. Starting with the rules means the
  first agent is checked against them from its first run.
- **Let an agent judge eligibility from the records.** Rejected: the rules are arithmetic on
  dates, hours and headcounts, and a model's answer can't be defended to an auditor the way a
  test can.
- **Model state leave laws now.** Rejected for version 1, as the design says: a state with its
  own law only sends the request to a person.

## Consequences

- The positions taken on HR's open questions are assumptions, listed with the data, and change
  when HR answers.
- Some FMLA details are simplified (HM-21 gaps): the rolling year is measured once at the
  leave's start, holidays don't move the notice deadline, and an integrated employer isn't
  modelled.
- Nothing here is in the audit log or the panel's case pages yet: those arrive with the agents
  and the approval gate.

## Evidence

`tests/test_hr_leave.py`: every request matches its answer key, every routing reason appears in
an answer key, the rules are tested on their boundaries, a missing fact is never a failure,
flags on a clean request send it to HR review, and only HR managers and directors may deny or
extend. A first review found 18 problems, all fixed or logged as gaps, and a review of those
fixes 10 more, all fixed (docs/review/log.md, Review 8).
