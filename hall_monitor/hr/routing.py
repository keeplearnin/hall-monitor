"""Who decides a leave request: a code rule, not a model.

The roles follow how a branch company already works:

- local_manager: the employee's local (branch) manager. Prepares every request with the
  employee and may approve only the plain light-path case;
- hr_business_partner: the senior HR role. Denies, decides partial approvals, and decides every
  request the rule sends to HR review;
- hr_director: extends leave past the entitlement and decides a case with a retaliation risk.

Only one kind of request goes to the light path (route manager_sign_off): an eligible employee,
a clear qualifying reason, within the remaining entitlement, with no flag raised. Even that is a
draft for the local manager to sign, never an approval without a person (the design's question
1, answered conservatively until HR says otherwise). Everything else goes to HR review, and the
rule names the least senior role that may decide it: an HR business partner for every flag
(a local manager never decides a flagged request), an HR director for leave past the
entitlement, a retaliation risk, or a request the rules never ran on. The sign-off table in
the design for HR review (a Claude Doc, not in this repo) set the director's cases; the rest is
this module's choice. The sign-off (hr/signoff.py) enforces the role: a decision by anyone less
senior is refused.
"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict

from hall_monitor.hr.eligibility import BONDING, Assessment, bonding_expires
from hall_monitor.hr.models import Company, Employee, LeaveReason, LeaveRequest, Relationship


class Role(IntEnum):
    """Ordered by seniority. Logs written before the roles were renamed may name
    leave_specialist or hr_manager: those aren't roles here, and sign-off refuses them."""

    LOCAL_MANAGER = 1
    HR_BUSINESS_PARTNER = 2
    HR_DIRECTOR = 3

    @property
    def label(self) -> str:
        return self.name.lower()


# States with their own family or medical leave law, to route on, checked against where the
# employee works and where they live. Not a legal list: a person checks which law applies.
# (Design: state laws are out of scope for version 1.)
STATE_LEAVE_LAW_STATES = frozenset("CA CO CT DC DE HI MA MD ME MN NJ NY OR RI VT WA".split())

# Every reason the rule gives: what it means, and the least senior role that may decide it.
# No reason names a local manager: they decide only a request with no reason at all.
REASONS: dict[str, tuple[str, Role]] = {
    "employer_not_covered": ("the employer isn't covered by FMLA", Role.HR_BUSINESS_PARTNER),
    "short_service": ("under 12 months of service", Role.HR_BUSINESS_PARTNER),
    "low_hours": ("under 1,250 hours in the last 12 months", Role.HR_BUSINESS_PARTNER),
    "small_worksite": ("under 50 employees within 75 miles", Role.HR_BUSINESS_PARTNER),
    "facts_missing": ("a record the rules need is missing", Role.HR_BUSINESS_PARTNER),
    "reason_unclear": ("the reason for leave isn't clear", Role.HR_BUSINESS_PARTNER),
    "adult_child": (
        "care for an adult child: a person checks whether they can care for themselves",
        Role.HR_BUSINESS_PARTNER,
    ),
    "family_member_not_covered": (
        "the family member isn't one FMLA covers",
        Role.HR_BUSINESS_PARTNER,
    ),
    "bonding_window": (
        "bonding leave must end within 12 months of the birth or placement",
        Role.HR_BUSINESS_PARTNER,
    ),
    "past_entitlement": ("more leave than the entitlement left", Role.HR_DIRECTOR),
    "intermittent": ("intermittent or reduced-schedule leave", Role.HR_BUSINESS_PARTNER),
    "intermittent_past_entitlement": (
        "intermittent leave whose dates hold more workweeks than are left: it may run past the "
        "entitlement",
        Role.HR_DIRECTOR,
    ),
    "retaliation_risk": (
        "discipline, a complaint or a termination is pending (retaliation risk)",
        Role.HR_DIRECTOR,
    ),
    "possible_accommodation": (
        "a possible disability or pregnancy accommodation (ADA or PWFA interactive process)",
        Role.HR_BUSINESS_PARTNER,
    ),
    "state_law": ("a state leave law may apply", Role.HR_BUSINESS_PARTNER),
    "key_employee": ("a key employee: reinstatement may be refused", Role.HR_BUSINESS_PARTNER),
    "spouses_same_employer": (
        "the employee's spouse works here too, so leave for this reason may be shared",
        Role.HR_BUSINESS_PARTNER,
    ),
    "before_birth": (
        "birth bonding leave starting before the birth: a person decides whether it is "
        "prenatal care, a health condition or bonding",
        Role.HR_BUSINESS_PARTNER,
    ),
    "text_addresses_ai": (
        "the request contains text aimed at the AI (possible prompt injection)",
        Role.HR_BUSINESS_PARTNER,
    ),
    "pipeline_stopped": (
        "the request couldn't be prepared (an agent failed, was stopped or isn't approved)",
        Role.HR_BUSINESS_PARTNER,
    ),
    # The leave supervisor's (ADR 0028): each only adds review.
    "intake_disagreed": (
        "two independent intakes of the request disagreed; the rules ran on the first",
        Role.HR_BUSINESS_PARTNER,
    ),
    "supervisor_handed_off": (
        "the leave supervisor handed the request to HR review",
        Role.HR_BUSINESS_PARTNER,
    ),
    # Without the rules nobody knows which role they would name, so the most senior decides
    # (found by the offline eval, ADR 0028).
    "assessment_skipped": (
        "the request was routed before the rules ran, so the most senior role decides",
        Role.HR_DIRECTOR,
    ),
    # Cost governance (HM-34): refused before any model call, so the rules never ran.
    "spend_refused": (
        "the leave spending cap was reached, its budgets aren't approved or the spend ledger "
        "couldn't be reached; nothing was extracted, so the most senior role decides",
        Role.HR_DIRECTOR,
    ),
    "supervisor_failed": (
        "the leave supervisor failed, was stopped, ran out of budget or kept choosing steps "
        "code refused",
        Role.HR_BUSINESS_PARTNER,
    ),
    "military_status_unverified": (
        "military leave: a person checks the family member's covered active duty or covered "
        "servicemember status, which the records don't carry",
        Role.HR_BUSINESS_PARTNER,
    ),
    "caregiver_history": (
        "earlier military caregiver leave: a person checks whether it was for the same "
        "servicemember and injury",
        Role.HR_BUSINESS_PARTNER,
    ),
}


# Reasons only the pipeline gives (hr/pipeline.py), not the rules on a structured request.
PIPELINE_REASONS = frozenset(
    {
        "text_addresses_ai",
        "pipeline_stopped",
        "intake_disagreed",
        "supervisor_handed_off",
        "assessment_skipped",
        "supervisor_failed",
        "spend_refused",
    }
)


# The two routes, and the role labels, sign-off accepts; anything else (a value from a log
# written before the roles were renamed, say) is refused, never guessed at.
ROUTES = ("manager_sign_off", "hr_review")


def role_named(label: object) -> Role | None:
    """The role a label names, or None for one the rules don't know (an old or bad value)."""
    return next((r for r in Role if r.label == label), None)


class LeaveRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str  # one of ROUTES
    decided_by: str  # the least senior role that may decide
    reasons: list[str]  # codes from REASONS, sorted
    explained: list[str]


def route_leave(
    assessment: Assessment, request: LeaveRequest, employee: Employee, company: Company
) -> LeaveRoute:
    # Route on the answer, not the code: a check that didn't pass always reaches a person.
    codes = {c.code or "facts_missing" for c in assessment.checks if c.passed is not True}
    expires = bonding_expires(request)
    if request.reason in BONDING and expires is not None and request.end >= expires:
        codes.add("bonding_window")  # the part after the 12 months doesn't qualify
    if request.intermittent:
        codes.add("intermittent")
    if employee.pending_adverse_action:
        codes.add("retaliation_risk")
    if request.possible_accommodation:
        codes.add("possible_accommodation")
    # Where they work or where they live: a state not on record can't be ruled out.
    site = next((s for s in company.worksites if s.id == employee.worksite_id), None)
    if site is None or employee.home_state is None:
        codes.add("facts_missing")
    if {site.state if site else None, employee.home_state} & STATE_LEAVE_LAW_STATES:
        codes.add("state_law")
    if employee.key_employee:
        codes.add("key_employee")
    # Spouses at one employer may be limited to a combined entitlement for bonding and for care
    # of a parent (825.120(a)(3), 825.121(a)(3)), and to 26 weeks for military caregiver leave
    # (825.127(f)).
    if employee.spouse_employee_id and (
        request.reason in BONDING
        or request.reason is LeaveReason.MILITARY_CAREGIVER
        or (
            request.reason is LeaveReason.CARE_FOR_FAMILY_MEMBER
            and request.family_relationship is Relationship.PARENT
        )
    ):
        codes.add("spouses_same_employer")
    reasons = sorted(codes)
    role = max((REASONS[c][1] for c in reasons), default=Role.LOCAL_MANAGER)
    return LeaveRoute(
        route="hr_review" if reasons else "manager_sign_off",
        decided_by=role.label,
        reasons=reasons,
        explained=[REASONS[c][0] for c in reasons],
    )
