# ADR 0034: Leave roles follow the branch: local manager, HR business partner, director

- **Status:** Accepted (HR example, review 38)
- **Control:** [HM-21](../controls.md#hm-21), [HM-23](../controls.md#hm-23)

## Context

The first leave slices ([ADR 0017](0017-leave-rules-in-code.md),
[ADR 0019](0019-signed-hr-sign-off.md)) named three HR roles: a leave specialist who approved
most requests, an HR manager who denied, and an HR director who extended. A company that runs
through branches doesn't have leave specialists: the employee's local (branch) manager is the
person who hears about the leave first and prepares it with them, and HR is a business partner
who covers several branches, with a director above. Modelling roles the firm doesn't have would
make the role check enforce an org chart nobody works to.

## Decision

- **Three roles, ordered by seniority** (`hall_monitor/hr/routing.py` `Role`):
  - **local_manager** prepares every request with the employee and may approve only the plain
    light-path case: an eligible employee, a clear qualifying reason, within the weeks left,
    with nothing flagged. That route is now called `manager_sign_off`.
  - **hr_business_partner** denies, decides partial approvals, and decides every request the
    rule sends to HR review: every routing reason names an HR business partner or above, so a
    local manager never decides a flagged request.
  - **hr_director** extends leave past the entitlement, decides a case with a retaliation risk,
    and decides a request the agentic path routed before the rules ran or one refused at the
    spending cap.
- **Military leave goes to a person, who may attest.** Qualifying-exigency and military
  caregiver leave need the family member's covered active duty or covered servicemember status,
  which the records don't carry, so the reason check is "can't decide"
  (`military_status_unverified`). An HR business partner or above may approve it with a written
  reason of at least 20 characters, their attestation that they checked the status; the rest of
  eligibility must still pass, and it can't extend leave.
- **Old values are refused, never mapped.** A log written before the rename may hold
  `leave_specialist`, `hr_manager` or `specialist_sign_off`; sign-off refuses such a route or
  role cleanly rather than guessing which new role it meant.
- **The note follows the reader.** The leave explanation prompt (v2, manifest 0.2.0) addresses
  the person who decides: the local manager on the light path, HR for anything flagged.

## Alternatives considered

- **Keep the leave specialist and map it to the local manager.** Rejected: the specialist could
  decide many flagged cases (an adult child, intermittent leave, a state law); a branch manager
  isn't trained for those, so every flag now needs HR.
- **Let the local manager deny the excess of a request past the entitlement.** Rejected: a
  denial is HR's, whatever its size.
- **Map old log values to the new roles.** Rejected: a decision checked against a guessed role
  is a decision the rules didn't check.

## Consequences

- More requests reach HR: in the 28 synthetic requests, 7 take the local manager's light path,
  19 go to an HR business partner and 2 to an HR director.
- The roles and their limits are modelled on a branch company and are this code's choice, not
  confirmed by an HR team; the director's cases still come from the design for HR review.
- A request in a log written before the rename can't be decided through the sign-off: preparing
  it again is refused too, so it is handled outside this step.
- The military attestation is a written reason, signed and stored; nothing checks it against a
  record of the family member's status.

## Evidence

`tests/test_hr_leave.py` (`test_a_local_manager_never_decides_a_flagged_request`,
`test_the_light_path_is_the_local_manager_s`,
`test_military_leave_without_a_verified_military_status_goes_to_a_person`) and
`tests/test_hr_signoff.py` (`test_a_local_manager_may_not_decide_a_flagged_request`,
`test_only_an_hr_business_partner_or_above_may_deny_even_on_the_light_path`,
`test_extending_needs_a_director_even_on_the_light_path`, the military attestation tests and
`test_an_old_role_or_route_in_an_evidence_pack_is_refused_cleanly`). The live run of the
agentic leave path routed all 28 requests to the roles their answer keys name
(`evals/results/hr-agentic-live/results.json`; one run, one author's requests and keys).
