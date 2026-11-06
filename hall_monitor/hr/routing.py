"""Who decides a leave request: a code rule, not a model.

Only one kind of request goes to the light path: an eligible employee, a clear qualifying
reason, within the remaining entitlement, with no flag raised. Even that is a draft for a leave
specialist to sign, never an approval without a person (the design's question 1, answered
conservatively until HR says otherwise). Everything else goes to HR review, and the rule names
the least senior role that may decide it, from the design's sign-off table: a leave specialist
may approve within the entitlement or ask for more; only an HR manager may deny or find an
employee ineligible; only an HR director may extend leave past the entitlement or decide a case
with a retaliation risk.
"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict

from hall_monitor.hr.eligibility import BONDING, Assessment
from hall_monitor.hr.models import Company, Employee, LeaveReason, LeaveRequest, Relationship


class Role(IntEnum):
    LEAVE_SPECIALIST = 1
    HR_MANAGER = 2
    HR_DIRECTOR = 3

    @property
    def label(self) -> str:
        return self.name.lower()


# States with their own family or medical leave law, to route on. Not a legal list: a person
# checks which law applies. (Design: state laws are out of scope for version 1.)
STATE_LEAVE_LAW_STATES = frozenset("CA CO CT DC DE HI MA MD ME MN NJ NY OR RI VT WA".split())

# Every reason the rule gives: what it means, and the least senior role that may decide it.
REASONS: dict[str, tuple[str, Role]] = {
    "employer_not_covered": ("the employer isn't covered by FMLA", Role.HR_MANAGER),
    "short_service": ("under 12 months of service", Role.HR_MANAGER),
    "low_hours": ("under 1,250 hours in the last 12 months", Role.HR_MANAGER),
    "small_worksite": ("under 50 employees within 75 miles", Role.HR_MANAGER),
    "facts_missing": ("a record the rules need is missing", Role.LEAVE_SPECIALIST),
    "reason_unclear": ("the reason for leave isn't clear", Role.LEAVE_SPECIALIST),
    "adult_child": (
        "care for an adult child: a person checks whether they can care for themselves",
        Role.LEAVE_SPECIALIST,
    ),
    "family_member_not_covered": ("the family member isn't one FMLA covers", Role.HR_MANAGER),
    "bonding_window": (
        "bonding leave must end within 12 months of the birth or placement",
        Role.HR_MANAGER,
    ),
    "past_entitlement": ("more leave than the entitlement left", Role.HR_DIRECTOR),
    "intermittent": ("intermittent or reduced-schedule leave", Role.LEAVE_SPECIALIST),
    "retaliation_risk": (
        "discipline, a complaint or a termination is pending (retaliation risk)",
        Role.HR_DIRECTOR,
    ),
    "possible_accommodation": (
        "a possible disability or pregnancy accommodation (ADA or PWFA interactive process)",
        Role.LEAVE_SPECIALIST,
    ),
    "state_law": ("a state leave law may apply", Role.LEAVE_SPECIALIST),
    "key_employee": ("a key employee: reinstatement may be refused", Role.LEAVE_SPECIALIST),
    "parents_same_employer": (
        "both parents work here, so their leave for this reason is shared",
        Role.LEAVE_SPECIALIST,
    ),
}

SHARED_BY_SPOUSES = BONDING | {LeaveReason.CARE_FOR_FAMILY_MEMBER}  # 825.120(a)(3), 825.127(f)


class LeaveRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str  # "specialist_sign_off" or "hr_review"
    decided_by: str  # the least senior role that may decide
    reasons: list[str]  # codes from REASONS, sorted
    explained: list[str]


def route_leave(
    assessment: Assessment, request: LeaveRequest, employee: Employee, company: Company
) -> LeaveRoute:
    codes = {c.code for c in assessment.checks if c.passed is not True and c.code}
    requested = assessment.requested_weeks
    if (requested is not None and requested > assessment.remaining_weeks + 1e-9) or (
        assessment.remaining_weeks <= 0
    ):
        codes.add("past_entitlement")
    if request.intermittent:
        codes.add("intermittent")
    if employee.pending_adverse_action:
        codes.add("retaliation_risk")
    if request.possible_accommodation:
        codes.add("possible_accommodation")
    site = next((s for s in company.worksites if s.id == employee.worksite_id), None)
    if site is None or site.state in STATE_LEAVE_LAW_STATES:
        codes.add("state_law" if site else "facts_missing")
    if employee.key_employee:
        codes.add("key_employee")
    if employee.spouse_employee_id and (
        request.reason in BONDING
        or (
            request.reason in SHARED_BY_SPOUSES
            and request.family_relationship is Relationship.PARENT
        )
    ):
        codes.add("parents_same_employer")
    reasons = sorted(codes)
    role = max((REASONS[c][1] for c in reasons), default=Role.LEAVE_SPECIALIST)
    return LeaveRoute(
        route="hr_review" if reasons else "specialist_sign_off",
        decided_by=role.label,
        reasons=reasons,
        explained=[REASONS[c][0] for c in reasons],
    )
