"""FMLA eligibility and entitlement, checked by ordinary code against the records.

Our reading of the federal rules (29 CFR Part 825, DOL Fact Sheet #28), not legal advice, and not
yet reviewed by HR or an employment lawyer: state leave laws and company policy aren't modelled.
A model never decides any of this; an agent may explain the result but can't change it.

Each check passes, fails, or can't be decided from the records (passed=None); the routing rule
(hr/routing.py) sends every failure and every unknown to a person.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from hall_monitor.hr.models import (
    Company,
    Employee,
    Employer,
    LeaveReason,
    LeaveRequest,
    LeaveYearMethod,
    PastLeave,
    Relationship,
)

SERVICE_DAYS = 364  # 12 months of employment is 52 weeks (29 CFR 825.110(b)(3))
LONG_BREAK_YEARS = 7  # service before a break this long need not count (825.110(b)(1))
HOURS_OF_SERVICE = 1250  # in the 12 months before leave starts (825.110(c))
SITE_EMPLOYEES, SITE_MILES = 50, 75  # within 75 surface miles of the worksite (825.110(e), 825.111)
COVERED_WEEKS = 20  # weeks with 50 or more employees, this or last calendar year (825.104)
ENTITLEMENT_WEEKS = 12  # in the leave year (825.200(a))
CAREGIVER_WEEKS = 26  # in one 12-month period, measured forward (825.127(d)-(e))
NOTICE_BUSINESS_DAYS = 5  # eligibility notice after learning of the need for leave (825.300(b))

COVERED_CARE = {Relationship.SPOUSE, Relationship.CHILD, Relationship.PARENT}
COVERED_CAREGIVER = COVERED_CARE | {Relationship.NEXT_OF_KIN}
BONDING = {LeaveReason.BIRTH_BONDING, LeaveReason.PLACEMENT_BONDING}


class Check(BaseModel):
    """One rule, its answer and the facts it was decided on (IDs, dates, counts only)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    passed: bool | None  # None: the records can't decide it
    rule: str
    cite: str
    facts: dict
    code: str | None = None  # the routing reason when it didn't pass


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    employee_id: str
    eligible: bool | None  # the employee: employer, service, hours, worksite
    checks: list[Check]
    entitlement_weeks: float
    used_weeks: float
    remaining_weeks: float
    requested_weeks: float | None  # None for intermittent or reduced-schedule leave
    notice_due: date


def years_after(day: date, years: int) -> date:
    """The same date `years` later (or earlier); 29 February becomes 28 February."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _overlap_days(start: date, end: date, window_start: date, window_end: date) -> int:
    """Days of [start, end] (inclusive) inside [window_start, window_end)."""
    first, last = max(start, window_start), min(end, window_end - timedelta(days=1))
    return max(0, (last - first).days + 1)


# --- The employee ----------------------------------------------------------------------------


def check_employer(employer: Employer, notice: date) -> Check:
    weeks = {
        year: employer.weeks_with_50_or_more_employees.get(year, 0)
        for year in (notice.year - 1, notice.year)
    }
    covered = employer.public_agency or any(w >= COVERED_WEEKS for w in weeks.values())
    return Check(
        id="employer",
        passed=covered,
        rule="a public agency, or 50 or more employees in 20 or more workweeks this or last "
        "calendar year",
        cite="29 CFR 825.104",
        facts={"public_agency": employer.public_agency, "weeks_with_50_or_more": weeks},
        code=None if covered else "employer_not_covered",
    )


def check_service(employee: Employee, start: date) -> Check:
    periods = sorted(employee.employment, key=lambda p: p.start)
    counted, excluded = list(periods), 0
    if not employee.count_service_before_long_break:
        for i in range(len(periods) - 1, 0, -1):
            ended = periods[i - 1].end
            if ended is not None and periods[i].start >= years_after(ended, LONG_BREAK_YEARS):
                excluded = sum(_service_days(p, start) for p in periods[:i])
                counted = periods[i:]
                break
    days = sum(_service_days(p, start) for p in counted)
    return Check(
        id="service",
        passed=days >= SERVICE_DAYS,
        rule=f"12 months (52 weeks, {SERVICE_DAYS} days) of employment before leave starts, "
        f"not counting service before a break of {LONG_BREAK_YEARS} years or more",
        cite="29 CFR 825.110(b)",
        facts={"days_counted": days, "days_before_a_long_break": excluded},
        code=None if days >= SERVICE_DAYS else "short_service",
    )


def _service_days(period, start: date) -> int:
    end = start if period.end is None else min(period.end, start)
    return max(0, (end - period.start).days)


def _saturdays(first: date, last: date) -> int:
    """Saturdays from first to last inclusive, both Saturdays."""
    return 0 if last < first else (last - first).days // 7 + 1


def check_hours(employee: Employee, start: date) -> Check:
    window_start = years_after(start, -1)
    rule = f"{HOURS_OF_SERVICE:,} hours worked in the 12 months before leave starts"
    if employee.hours_ledger is None:
        return Check(
            id="hours",
            passed=None,
            rule=rule,
            cite="29 CFR 825.110(c)",
            facts={"hours_ledger": None},
            code="facts_missing",
        )
    # Weeks ending (Saturday) inside [window_start, start).
    first = window_start + timedelta(days=(5 - window_start.weekday()) % 7)
    day_before = start - timedelta(days=1)
    last = day_before - timedelta(days=(day_before.weekday() - 5) % 7)
    hours = sum(
        entry.hours_per_week
        * _saturdays(max(entry.weeks_ending_from, first), min(entry.weeks_ending_to, last))
        for entry in employee.hours_ledger
    )
    return Check(
        id="hours",
        passed=hours >= HOURS_OF_SERVICE,
        rule=rule,
        cite="29 CFR 825.110(c)",
        facts={"hours": hours, "weeks_ending_from": first, "weeks_ending_to": last},
        code=None if hours >= HOURS_OF_SERVICE else "low_hours",
    )


def check_worksite(employee: Employee, company: Company) -> Check:
    rule = f"{SITE_EMPLOYEES} employees within {SITE_MILES} surface miles of the worksite"
    sites = {s.id: s for s in company.worksites if s.employer_id == employee.employer_id}
    if employee.worksite_id not in sites:
        return Check(
            id="worksite",
            passed=None,
            rule=rule,
            cite="29 CFR 825.110(e), 825.111",
            facts={"worksite_id": employee.worksite_id, "found": False},
            code="facts_missing",
        )
    miles = {}
    for a, b, d in company.surface_miles:
        miles[(a, b)] = miles[(b, a)] = d
    home = employee.worksite_id
    near = [s for s in sites if s == home or miles.get((home, s), SITE_MILES + 1) <= SITE_MILES]
    count = sum(sites[s].headcount for s in near)
    return Check(
        id="worksite",
        passed=count >= SITE_EMPLOYEES,
        rule=rule,
        cite="29 CFR 825.110(e), 825.111",
        facts={"worksite_id": home, "sites_within_75_miles": sorted(near), "employees": count},
        code=None if count >= SITE_EMPLOYEES else "small_worksite",
    )


# --- The request -----------------------------------------------------------------------------


def check_reason(request: LeaveRequest) -> Check:
    passed, code = _reason(request)
    return Check(
        id="reason",
        passed=passed,
        rule="a qualifying reason: the employee's own serious health condition; care for a "
        "spouse, child or parent with one; bonding within 12 months of a birth or placement; a "
        "qualifying military exigency; or care for a covered servicemember",
        cite="29 CFR 825.112, 825.120-127",
        facts={
            "reason": request.reason.value,
            "relationship": request.family_relationship,
            "event_date": request.event_date,
        },
        code=code,
    )


def _reason(request: LeaveRequest) -> tuple[bool | None, str | None]:
    reason, relation = request.reason, request.family_relationship
    if reason is LeaveReason.UNCLEAR:
        return None, "reason_unclear"
    if reason is LeaveReason.OWN_SERIOUS_HEALTH_CONDITION:
        return True, None
    if reason in BONDING:
        if request.event_date is None:
            return None, "reason_unclear"
        # Bonding leave must end within 12 months of the birth or placement (825.120(a)(2)).
        if request.end >= years_after(request.event_date, 1):
            return False, "bonding_window"
        return True, None
    if relation is None:
        return None, "reason_unclear"
    if reason is LeaveReason.MILITARY_CAREGIVER:
        return (
            (True, None) if relation in COVERED_CAREGIVER else (False, "family_member_not_covered")
        )
    if relation not in COVERED_CARE:
        return False, "family_member_not_covered"
    if reason is LeaveReason.CARE_FOR_FAMILY_MEMBER and relation is Relationship.CHILD:
        # A child of 18 or over counts only if unable to care for themselves because of a
        # disability (825.122(d)); a person checks that.
        if request.family_member_age is None:
            return None, "reason_unclear"
        if request.family_member_age >= 18:
            if request.incapable_of_self_care is None:
                return None, "adult_child"
            if not request.incapable_of_self_care:
                return False, "family_member_not_covered"
    return True, None


def requested_weeks(request: LeaveRequest) -> float | None:
    """Workweeks of continuous full-time leave. Intermittent leave is counted as it is taken."""
    if request.intermittent:
        return None
    return round(((request.end - request.start).days + 1) / 7, 2)


def _used_days(taken: tuple[PastLeave, ...], window_start: date, window_end: date) -> int:
    return sum(_overlap_days(p.start, p.end, window_start, window_end) for p in taken)


def leave_year_start(employer: Employer, employee: Employee, start: date) -> date:
    """Where the 12-month period that holds `start` begins, by the employer's method."""
    method = employer.leave_year.method
    if method is LeaveYearMethod.CALENDAR_YEAR:
        return date(start.year, 1, 1)
    if method is LeaveYearMethod.FIXED_YEAR:
        month, day = (int(x) for x in employer.leave_year.starts.split("-"))
        begins = date(start.year, month, min(day, 28) if month == 2 else day)
        return begins if begins <= start else years_after(begins, -1)
    if method is LeaveYearMethod.ROLLING_BACKWARD:
        return years_after(start, -1)
    # Forward from first leave: a period starts with the first FMLA leave, and the next starts
    # with the first leave after that period ends; the request itself may start one.
    starts = sorted(p.start for p in employee.fmla_leave_taken if p.start < start)
    if not starts:
        return start
    begins = starts[0]
    while years_after(begins, 1) <= start:
        later = [s for s in starts if s >= years_after(begins, 1)]
        if not later:
            return start
        begins = later[0]
    return begins


def entitlement(request: LeaveRequest, employee: Employee, employer: Employer) -> tuple[int, float]:
    """(weeks of entitlement, weeks already used) when the requested leave starts."""
    taken = employee.fmla_leave_taken
    if request.reason is LeaveReason.MILITARY_CAREGIVER:
        # One 12-month period measured forward from the first day of caregiver leave, whatever
        # the employer's method; all FMLA leave in it counts towards the 26 weeks.
        caregiver = sorted(
            p.start
            for p in taken
            if p.reason is LeaveReason.MILITARY_CAREGIVER
            and p.start <= request.start < years_after(p.start, 1)
        )
        begins = caregiver[0] if caregiver else request.start
        return CAREGIVER_WEEKS, round(_used_days(taken, begins, request.start) / 7, 2)
    begins = leave_year_start(employer, employee, request.start)
    return ENTITLEMENT_WEEKS, round(_used_days(taken, begins, request.start) / 7, 2)


def notice_due(notice: date) -> date:
    """Five business days after notice. Weekends are skipped; holidays aren't modelled."""
    day, left = notice, NOTICE_BUSINESS_DAYS
    while left:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def assess(request: LeaveRequest, employee: Employee, company: Company) -> Assessment:
    employer = next(e for e in company.employers if e.id == employee.employer_id)
    checks = [
        check_employer(employer, request.notice_date),
        check_service(employee, request.start),
        check_hours(employee, request.start),
        check_worksite(employee, company),
        check_reason(request),
    ]
    person = [c.passed for c in checks if c.id != "reason"]
    eligible = False if False in person else None if None in person else True
    weeks, used = entitlement(request, employee, employer)
    return Assessment(
        request_id=request.id,
        employee_id=employee.id,
        eligible=eligible,
        checks=checks,
        entitlement_weeks=weeks,
        used_weeks=used,
        remaining_weeks=round(weeks - used, 2),
        requested_weeks=requested_weeks(request),
        notice_due=notice_due(request.notice_date),
    )
