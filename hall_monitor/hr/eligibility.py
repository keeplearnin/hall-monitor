"""FMLA eligibility and entitlement, checked by ordinary code against the records.

Our reading of the federal rules (29 CFR Part 825, DOL Fact Sheet #28), not legal advice, and not
yet reviewed by HR or an employment lawyer: state leave laws and company policy aren't modelled.
A model never decides any of this; an agent may explain the result but can't change it.

Each check passes, fails, or can't be decided from the records (passed=None). A missing fact is
never filled in: a missing coverage year, distance, hours week or caregiver history is "can't
decide", not a failure. The routing rule (hr/routing.py) sends every failure and every unknown
to a person.
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
    Relationship,
)

SERVICE_WEEKS = 52  # 12 months of employment is 52 weeks; any week on the payroll counts
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
ONE_DAY = timedelta(days=1)


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
    used_weeks: float | None  # None when the records can't say
    remaining_weeks: float | None
    requested_weeks: float | None  # None for intermittent or reduced-schedule leave
    notice_due: date


def years_after(day: date, years: int) -> date:
    """The same date `years` later (or earlier); 29 February becomes 28 February."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _days(first: date, last: date):
    """Every date from first to last inclusive."""
    for n in range((last - first).days + 1):
        yield first + timedelta(days=n)


def _check(id_: str, passed: bool | None, rule: str, cite: str, facts: dict, code: str) -> Check:
    return Check(
        id=id_, passed=passed, rule=rule, cite=cite, facts=facts, code=None if passed else code
    )


def _unknown(id_: str, rule: str, cite: str, facts: dict) -> Check:
    return Check(id=id_, passed=None, rule=rule, cite=cite, facts=facts, code="facts_missing")


# --- The employee ----------------------------------------------------------------------------


def check_employer(employer: Employer | None, notice: date) -> Check:
    """Coverage is judged on the calendar year of the notice and the year before (an
    assumption: data/synthetic/hr/README.md)."""
    rule = "a public agency, or 50 or more employees in 20 or more workweeks this or last year"
    cite = "29 CFR 825.104"
    if employer is None:
        return _unknown("employer", rule, cite, {"employer": None})
    years = (notice.year - 1, notice.year)
    weeks = {y: employer.weeks_with_50_or_more_employees.get(y) for y in years}
    if employer.public_agency or any(w is not None and w >= COVERED_WEEKS for w in weeks.values()):
        covered = True
    elif None in weeks.values():
        return _unknown("employer", rule, cite, {"weeks_with_50_or_more": weeks})
    else:
        covered = False
    facts = {"public_agency": employer.public_agency, "weeks_with_50_or_more": weeks}
    return _check("employer", covered, rule, cite, facts, "employer_not_covered")


def _sunday(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _payroll_weeks(period, start: date) -> set[date]:
    """The weeks (Sunday to Saturday, by their Sunday) with a day of this period before start."""
    last = (start if period.end is None else min(period.end, start)) - ONE_DAY
    if last < period.start:
        return set()
    return set(_days_step(_sunday(period.start), _sunday(last)))


def _days_step(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=7)


def check_service(employee: Employee, start: date) -> Check:
    """Any week the employee was on the payroll for any part of counts (825.110(b)(3))."""
    periods = sorted(employee.employment, key=lambda p: p.start)
    counted, excluded = list(periods), set()
    if not employee.count_service_before_long_break:
        for i in range(len(periods) - 1, 0, -1):
            ended = periods[i - 1].end
            if ended is not None and periods[i].start >= years_after(ended, LONG_BREAK_YEARS):
                excluded = set().union(*(_payroll_weeks(p, start) for p in periods[:i]))
                counted = periods[i:]
                break
    weeks = len(set().union(*(_payroll_weeks(p, start) for p in counted)))
    facts = {"weeks_counted": weeks, "weeks_before_a_long_break": len(excluded)}
    return _check(
        "service",
        weeks >= SERVICE_WEEKS,
        f"{SERVICE_WEEKS} weeks of employment before leave starts (any week on the payroll "
        f"counts), not counting service before a break of {LONG_BREAK_YEARS} years or more",
        "29 CFR 825.110(b)",
        facts,
        "short_service",
    )


def _employed_on(employee: Employee, day: date) -> bool:
    return any(p.start <= day and (p.end is None or day < p.end) for p in employee.employment)


def check_hours(employee: Employee, start: date) -> Check:
    """Hours worked in the weeks ending (Saturday) inside the 12 months before leave starts. A
    week the employee was on the payroll that the ledger doesn't cover can't be counted as zero:
    the employer has to show the hours weren't worked (825.110(c)(3))."""
    rule = f"{HOURS_OF_SERVICE:,} hours worked in the 12 months before leave starts"
    cite = "29 CFR 825.110(c)"
    if employee.hours_ledger is None:
        return _unknown("hours", rule, cite, {"hours_ledger": None})
    window_start = years_after(start, -1)
    first = window_start + timedelta(days=(5 - window_start.weekday()) % 7)
    last = (start - ONE_DAY) - timedelta(days=((start - ONE_DAY).weekday() - 5) % 7)
    weeks = list(_days_step(first, last))
    ledger = {}
    for entry in employee.hours_ledger:
        for saturday in _days_step(entry.weeks_ending_from, entry.weeks_ending_to):
            ledger[saturday] = entry.hours_per_week
    hours = sum(ledger.get(w, 0) for w in weeks)
    missing = [
        w
        for w in weeks
        if w not in ledger
        and any(_employed_on(employee, d) for d in _days(w - timedelta(days=6), w) if d < start)
    ]
    facts = {"hours": hours, "weeks_ending_from": first, "weeks_ending_to": last}
    if hours < HOURS_OF_SERVICE and missing:
        return _unknown("hours", rule, cite, facts | {"weeks_missing": len(missing)})
    return _check("hours", hours >= HOURS_OF_SERVICE, rule, cite, facts, "low_hours")


def check_worksite(employee: Employee, company: Company) -> Check:
    rule = f"{SITE_EMPLOYEES} employees within {SITE_MILES} surface miles of the worksite"
    cite = "29 CFR 825.110(e), 825.111"
    sites = {s.id: s for s in company.worksites if s.employer_id == employee.employer_id}
    home = employee.worksite_id
    if home not in sites:
        return _unknown("worksite", rule, cite, {"worksite_id": home, "found": False})
    miles = {}
    for a, b, d in company.surface_miles:
        miles[(a, b)] = miles[(b, a)] = d
    unmeasured = sorted(s for s in sites if s != home and (home, s) not in miles)
    near = [s for s in sites if s == home or miles.get((home, s), SITE_MILES + 1) <= SITE_MILES]
    count = sum(sites[s].headcount for s in near)
    facts = {"worksite_id": home, "sites_within_75_miles": sorted(near), "employees": count}
    if count < SITE_EMPLOYEES and unmeasured:
        return _unknown("worksite", rule, cite, facts | {"sites_without_a_distance": unmeasured})
    return _check("worksite", count >= SITE_EMPLOYEES, rule, cite, facts, "small_worksite")


# --- The request -----------------------------------------------------------------------------


def bonding_expires(request: LeaveRequest) -> date | None:
    """The first day bonding leave no longer qualifies: 12 months after the birth or placement
    (825.120(a)(2), 825.121(a)(2)). From 29 February that is 1 March, not 28 February."""
    event = request.event_date
    if event is None:
        return None
    if (event.month, event.day) == (2, 29):
        return date(event.year + 1, 3, 1)
    return years_after(event, 1)


def check_reason(request: LeaveRequest) -> Check:
    passed, code = _reason(request)
    expires = bonding_expires(request)
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
            "starts_inside_the_bonding_year": None if expires is None else request.start < expires,
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
        # Before a birth, leave is prenatal care or a health condition, not bonding
        # (825.120(a)(4)-(5)); leave before a placement can qualify (825.121(a)(1)).
        if reason is LeaveReason.BIRTH_BONDING and request.start < request.event_date:
            return None, "before_birth"
        # Leave starting after the 12 months doesn't qualify; leave running past them qualifies
        # up to their end, and the routing rule sends the rest to a person.
        return (
            (False, "bonding_window") if request.start >= bonding_expires(request) else (True, None)
        )
    if relation is None or relation is Relationship.OTHER:
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
        if request.family_member_under_18 is None:
            return None, "reason_unclear"
        if not request.family_member_under_18:
            if request.incapable_of_self_care is None:
                return None, "adult_child"
            if not request.incapable_of_self_care:
                return False, "family_member_not_covered"
    return True, None


# --- Entitlement -----------------------------------------------------------------------------


def _workweeks(days: int, employee: Employee) -> float:
    return round(days / len(employee.workdays), 2)


def _leave_days(employee: Employee, before: date, reason: LeaveReason | None = None) -> list[date]:
    """Workdays of FMLA leave already taken before `before`, in order."""
    return sorted(
        d
        for p in employee.fmla_leave_taken
        if reason is None or p.reason is reason
        for d in _days(p.start, min(p.end, before - ONE_DAY))
        if d.weekday() in employee.workdays
    )


def requested_weeks(request: LeaveRequest, employee: Employee) -> float | None:
    """Workweeks of continuous leave: its workdays over the normal workweek. Intermittent leave
    is counted as it is taken."""
    if request.intermittent:
        return None
    days = sum(1 for d in _days(request.start, request.end) if d.weekday() in employee.workdays)
    return _workweeks(days, employee)


def leave_year_start(employer: Employer, employee: Employee, start: date) -> date | None:
    """Where the 12-month period that holds `start` begins, by the employer's method; None for
    a method the rules don't know. Caregiver leave has its own period and isn't counted here."""
    method = employer.leave_year.method
    if method == LeaveYearMethod.CALENDAR_YEAR:
        return date(start.year, 1, 1)
    if method == LeaveYearMethod.FIXED_YEAR:
        month, day = (int(x) for x in employer.leave_year.starts.split("-"))
        begins = date(start.year, month, day)
        return begins if begins <= start else years_after(begins, -1)
    if method == LeaveYearMethod.ROLLING_BACKWARD:
        return years_after(start, -1)
    if method != LeaveYearMethod.FORWARD_FROM_FIRST_LEAVE:
        return None
    # Forward from first leave: a period starts on the first day of FMLA leave, and the next
    # starts on the first day of leave after that period ends, which may be inside a leave that
    # ran past it, or the request itself.
    begins = None
    for day in _other_leave_days(employee, start):
        if begins is None or day >= years_after(begins, 1):
            begins = day
    if begins is None or start >= years_after(begins, 1):
        return start
    return begins


def _other_leave_days(employee: Employee, before: date) -> list[date]:
    """Workdays of FMLA leave taken for reasons other than military caregiving."""
    caregiver = set(_leave_days(employee, before, LeaveReason.MILITARY_CAREGIVER))
    return [d for d in _leave_days(employee, before) if d not in caregiver]


def caregiver_period(employee: Employee, before: date) -> date | None:
    """The first workday of the latest military caregiver period: one starts with the first day
    of caregiver leave, and caregiver leave after it ends starts another (for another
    servicemember or injury, which the records don't say)."""
    begins = None
    for day in _leave_days(employee, before, LeaveReason.MILITARY_CAREGIVER):
        if begins is None or day >= years_after(begins, 1):
            begins = day
    return begins


def entitlement(
    request: LeaveRequest, employee: Employee, employer: Employer | None
) -> tuple[int, float | None]:
    """(weeks of entitlement, workweeks already used against it) when the requested leave
    starts. Used is None when the records can't say.

    Military caregiver leave: 26 weeks in one 12-month period measured forward from its first
    day, whatever the employer's method, with all FMLA leave in the period counting (825.127(e)).
    Other leave: 12 weeks in the employer's leave year, counting leave for other reasons only;
    and while a caregiver period is open, also no more than the 26 weeks less all FMLA leave in
    it (825.200(f)). The report is against whichever limit leaves less.
    """
    period = caregiver_period(employee, request.start)
    in_period = period is not None and request.start < years_after(period, 1)
    all_in_period = (
        [d for d in _leave_days(employee, request.start) if d >= period] if in_period else []
    )
    if request.reason is LeaveReason.MILITARY_CAREGIVER:
        if period is None:
            return CAREGIVER_WEEKS, 0.0
        if not in_period:
            # The 26 weeks are per servicemember and per injury: a person checks whether this
            # request starts a new period.
            return CAREGIVER_WEEKS, None
        return CAREGIVER_WEEKS, _workweeks(len(all_in_period), employee)
    begins = None if employer is None else leave_year_start(employer, employee, request.start)
    if begins is None:
        return ENTITLEMENT_WEEKS, None
    other = [d for d in _other_leave_days(employee, request.start) if d >= begins]
    used = _workweeks(len(other), employee)
    if in_period:
        # Report against the 12 weeks, raised so that what is left is the smaller of the two.
        left_of_26 = CAREGIVER_WEEKS - _workweeks(len(all_in_period), employee)
        used = max(used, round(ENTITLEMENT_WEEKS - left_of_26, 2))
    return ENTITLEMENT_WEEKS, used


def check_entitlement(
    request: LeaveRequest, employee: Employee, employer: Employer | None
) -> tuple[Check, int, float | None, float | None]:
    weeks, used = entitlement(request, employee, employer)
    asked = requested_weeks(request, employee)
    remaining = None if used is None else round(weeks - used, 2)
    facts = {"entitlement_weeks": weeks, "used_weeks": used, "requested_weeks": asked}
    rule = (
        f"the leave fits in the weeks left: {ENTITLEMENT_WEEKS} in the leave year, or "
        f"{CAREGIVER_WEEKS} in one 12-month period for military caregiver leave"
    )
    cite = "29 CFR 825.200, 825.127"
    if remaining is None:
        code = "caregiver_history" if request.reason is LeaveReason.MILITARY_CAREGIVER else None
        check = _unknown("entitlement", rule, cite, facts)
        return check.model_copy(update={"code": code or "facts_missing"}), weeks, used, None
    fits = remaining > 0 if asked is None else asked <= remaining + 1e-9
    return (
        _check("entitlement", fits, rule, cite, facts, "past_entitlement"),
        weeks,
        used,
        remaining,
    )


def notice_due(notice: date) -> date:
    """Five business days after notice. Weekends are skipped; holidays aren't modelled."""
    day, left = notice, NOTICE_BUSINESS_DAYS
    while left:
        day += ONE_DAY
        if day.weekday() < 5:
            left -= 1
    return day


def assess(request: LeaveRequest, employee: Employee, company: Company) -> Assessment:
    employer = next((e for e in company.employers if e.id == employee.employer_id), None)
    entitled, weeks, used, remaining = check_entitlement(request, employee, employer)
    checks = [
        check_employer(employer, request.notice_date),
        check_service(employee, request.start),
        check_hours(employee, request.start),
        check_worksite(employee, company),
        check_reason(request),
        entitled,
    ]
    person = [c.passed for c in checks if c.id in ("employer", "service", "hours", "worksite")]
    eligible = False if False in person else None if None in person else True
    return Assessment(
        request_id=request.id,
        employee_id=employee.id,
        eligible=eligible,
        checks=checks,
        entitlement_weeks=weeks,
        used_weeks=used,
        remaining_weeks=remaining,
        requested_weeks=requested_weeks(request, employee),
        notice_due=notice_due(request.notice_date),
    )
