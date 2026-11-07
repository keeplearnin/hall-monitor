"""Records for the HR leave example: the employer, its worksites, employees and leave requests.

All synthetic (data/synthetic/hr/). The records hold what the eligibility and routing rules
read: no names, no diagnosis, no medical certificate, and only a yes or no for pending
discipline, a complaint or a termination. Some fields are still health information and are
kept as coarse as the rules allow: the reason category (an own serious health condition is
one), whether an adult child can't care for themselves because of a disability, and whether
there are signs of an accommodation need. A request never carries the condition itself.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = r"^[a-z0-9][a-z0-9_-]{0,63}$"


class LeaveYearMethod(StrEnum):
    """How the employer measures the 12-month period for the 12 weeks (29 CFR 825.200(b))."""

    CALENDAR_YEAR = "calendar_year"
    FIXED_YEAR = "fixed_year"  # a fixed 12-month year, such as a fiscal or anniversary year
    FORWARD_FROM_FIRST_LEAVE = "forward_from_first_leave"
    ROLLING_BACKWARD = "rolling_backward"


class LeaveYear(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: LeaveYearMethod
    # Month and day the fixed year starts, for fixed_year only (07-01 for a July fiscal year).
    # One date for the whole employer: a year from each employee's hire date isn't supported.
    starts: str | None = Field(default=None, pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @model_validator(mode="after")
    def _starts_only_for_a_fixed_year(self) -> LeaveYear:
        if (self.method is LeaveYearMethod.FIXED_YEAR) != (self.starts is not None):
            raise ValueError("starts is required for fixed_year and only for it")
        if self.starts is not None:
            month, day = (int(x) for x in self.starts.split("-"))
            date(2001, month, day)  # a date every year has: no 29 February, no 31 April
        return self


class Employer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=ID)
    name: str
    public_agency: bool  # public agencies and schools are covered whatever their size
    # Calendar workweeks with 50 or more employees on each working day, by calendar year.
    weeks_with_50_or_more_employees: dict[int, int]
    leave_year: LeaveYear


class Worksite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=ID)
    employer_id: str
    name: str
    state: str = Field(pattern=r"^[A-Z]{2}$")
    headcount: int = Field(ge=0)  # employees at the site when the employee gave notice


class Company(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    employers: tuple[Employer, ...]
    worksites: tuple[Worksite, ...]
    # Surface miles over public roads between two of one employer's sites. Every pair is listed;
    # a missing pair can't be decided (data/synthetic/hr/README.md).
    surface_miles: tuple[tuple[str, str, float], ...]


class EmploymentPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date | None  # None while still employed; the day after the last day worked otherwise

    @model_validator(mode="after")
    def _ordered(self) -> EmploymentPeriod:
        if self.end is not None and self.end < self.start:
            raise ValueError("an employment period can't end before it starts")
        return self


class HoursEntry(BaseModel):
    """Hours actually worked in each week ending (Saturday) from one date to another."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weeks_ending_from: date
    weeks_ending_to: date
    hours_per_week: float = Field(ge=0, le=168)

    @model_validator(mode="after")
    def _saturdays_in_order(self) -> HoursEntry:
        if self.weeks_ending_from.weekday() != 5 or self.weeks_ending_to.weekday() != 5:
            raise ValueError("weeks end on a Saturday")
        if self.weeks_ending_to < self.weeks_ending_from:
            raise ValueError("weeks_ending_to is before weeks_ending_from")
        return self


class LeaveReason(StrEnum):
    """Qualifying reasons (29 CFR 825.112), as a category only. Unclear means intake couldn't
    tell."""

    OWN_SERIOUS_HEALTH_CONDITION = "own_serious_health_condition"
    CARE_FOR_FAMILY_MEMBER = "care_for_family_member"
    BIRTH_BONDING = "birth_bonding"
    PLACEMENT_BONDING = "placement_bonding"  # adoption or foster care
    QUALIFYING_EXIGENCY = "qualifying_exigency"
    MILITARY_CAREGIVER = "military_caregiver"
    UNCLEAR = "unclear"


class Relationship(StrEnum):
    SPOUSE = "spouse"
    CHILD = "child"
    PARENT = "parent"
    NEXT_OF_KIN = "next_of_kin"  # of a covered servicemember, for military caregiver leave only
    # Anyone else, including someone who stood in place of a parent (in loco parentis), which
    # can qualify (29 CFR 825.122(c)-(d)), so a person decides it.
    PARENT_IN_LAW = "parent_in_law"
    SIBLING = "sibling"
    OTHER = "other"


class PastLeave(BaseModel):
    """FMLA leave already taken, full days, start and end inclusive. Its reason category is
    health information when it is the employee's own condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date
    reason: LeaveReason

    @model_validator(mode="after")
    def _ordered(self) -> PastLeave:
        if self.end < self.start:
            raise ValueError("leave can't end before it starts")
        return self


class Employee(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=ID)
    employer_id: str
    # The site they work at or report to; for someone working from home, the office they're
    # assigned to (29 CFR 825.111(a)(2)).
    worksite_id: str
    home_state: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    employment: tuple[EmploymentPeriod, ...] = Field(min_length=1)
    # The employer chose to count service before a break of 7 years or more (it may, and must
    # for military service or a written agreement: 29 CFR 825.110(b)).
    count_service_before_long_break: bool = False
    hours_ledger: tuple[HoursEntry, ...] | None  # None: no record of hours worked
    # The normal workweek, as weekday numbers (Monday 0): leave is counted in its workdays, so a
    # week of leave is a workweek and a single day is a fraction of one (29 CFR 825.205(b)).
    workdays: tuple[int, ...] = Field(default=(0, 1, 2, 3, 4), min_length=1)
    fmla_leave_taken: tuple[PastLeave, ...] = ()
    # A salaried employee among the highest-paid 10% within 75 miles (29 CFR 825.217).
    key_employee: bool = False
    spouse_employee_id: str | None = None
    # Discipline, a complaint, an investigation or a termination is pending: yes or no only.
    pending_adverse_action: bool = False

    @model_validator(mode="after")
    def _periods_counted_once(self) -> Employee:
        if not set(self.workdays) <= set(range(7)) or len(set(self.workdays)) != len(self.workdays):
            raise ValueError("workdays are distinct weekday numbers, Monday 0 to Sunday 6")
        periods = sorted(self.employment, key=lambda p: p.start)
        for before, after in zip(periods, periods[1:], strict=False):
            if before.end is None or after.start < before.end:
                raise ValueError("employment periods overlap, so some service would count twice")
        taken = sorted(self.fmla_leave_taken, key=lambda p: p.start)
        for before, after in zip(taken, taken[1:], strict=False):
            if after.start <= before.end:  # leave ends are inclusive
                raise ValueError("leave already taken overlaps, so some days would count twice")
        return self._weeks_counted_once()

    def _weeks_counted_once(self) -> Employee:
        entries = sorted(self.hours_ledger or (), key=lambda e: e.weeks_ending_from)
        for before, after in zip(entries, entries[1:], strict=False):
            if after.weeks_ending_from <= before.weeks_ending_to:
                raise ValueError("hours ledger entries overlap, so some weeks would count twice")
        return self


class LeaveRequest(BaseModel):
    """A request as the intake step would extract it: dates and a category, no condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=ID)
    employee_id: str
    notice_date: date  # when the employer learned of the need for leave
    start: date
    end: date  # inclusive
    intermittent: bool = False  # intermittent or a reduced schedule
    reason: LeaveReason
    family_relationship: Relationship | None = None
    family_member_under_18: bool | None = None  # for care of a child; no age is kept
    incapable_of_self_care: bool | None = None  # an adult child, because of a disability
    event_date: date | None = None  # the birth or placement, for bonding leave
    possible_accommodation: bool = False  # signs of an ADA or PWFA accommodation need

    @model_validator(mode="after")
    def _ordered(self) -> LeaveRequest:
        if self.end < self.start:
            raise ValueError("leave can't end before it starts")
        return self


class LeaveAnswer(BaseModel):
    """The answer key for one request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    eligible: bool | None
    failed_checks: list[str]
    unknown_checks: list[str]
    remaining_weeks: float | None
    requested_weeks: float | None
    route: str
    decided_by: str
    reasons: list[str]
    notice_due: date
    why: str


# --- What the agents read and write ----------------------------------------------------------


class LeaveMessage(BaseModel):
    """An employee's leave request as they wrote it: untrusted free text, with the IDs and time
    the HR system attaches. The text may hold a name, a condition and identifiers; it goes to
    the intake agent after redaction and nowhere else."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=ID)
    employee_id: str = Field(pattern=ID)
    submitted_at: datetime
    text: str = Field(min_length=1, max_length=10_000)


class LeaveIntake(BaseModel):
    """What the intake agent extracts. Dates, categories and yes-or-no answers only: there is
    no free-text field, so a diagnosis has nowhere to go."""

    model_config = ConfigDict(extra="forbid")

    start: date | None = Field(description="First day of leave, or null if not stated.")
    end: date | None = Field(description="Last day of leave (inclusive), or null if not stated.")
    intermittent: bool = Field(description="Leave taken in separate blocks or a reduced schedule.")
    reason: LeaveReason
    family_relationship: Relationship | None = Field(
        description="Who the employee will care for, when the reason is caring for someone."
    )
    family_member_under_18: bool | None = Field(
        description="For care of a child: whether the child is under 18, or null if not stated."
    )
    incapable_of_self_care: bool | None = Field(
        description="For care of an adult child: whether they can't care for themselves because "
        "of a disability, or null if not stated."
    )
    event_date: date | None = Field(
        description="For bonding: the date of the birth or placement, expected or past."
    )
    possible_accommodation: bool = Field(
        description="Signs the employee may need a disability or pregnancy accommodation."
    )

    @field_validator("start", "end", "event_date")
    @classmethod
    def _a_plausible_date(cls, value: date | None) -> date | None:
        # A date the rules can do arithmetic on; anything else is a bad extraction.
        if value is not None and not date(1900, 1, 1) <= value <= date(2199, 12, 31):
            raise ValueError("date out of range")
        return value


CheckId = Literal["employer", "service", "hours", "worksite", "reason", "entitlement"]


class CheckNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: CheckId
    text: str = Field(max_length=400, description="One plain sentence explaining the result.")


class LeaveExplanation(BaseModel):
    """The explanation agent's note for the leave specialist: advisory text only. The code's
    result decides; nothing here changes it. A list of notes, not a map: the API's structured
    output can't express a map with free keys."""

    model_config = ConfigDict(extra="forbid")

    checks: list[CheckNote] = Field(description="One note for each check given, by its id.")
    summary: str = Field(max_length=800, description="Two or three sentences for the specialist.")
