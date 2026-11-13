"""HM-21: leave eligibility, entitlement and routing are decided by code, on the boundaries."""

import ast
from datetime import date, timedelta
from pathlib import Path

import pytest

from hall_monitor.hr import __main__ as hr_cli
from hall_monitor.hr import eligibility
from hall_monitor.hr.data import load_answers, load_company, load_employees, load_requests
from hall_monitor.hr.eligibility import (
    Check,
    assess,
    check_employer,
    check_hours,
    check_reason,
    check_service,
    check_worksite,
    entitlement,
    notice_due,
    years_after,
)
from hall_monitor.hr.models import Employee, Employer, LeaveRequest
from hall_monitor.hr.routing import REASONS, route_leave

COMPANY, EMPLOYEES, REQUESTS, ANSWERS = (
    load_company(),
    load_employees(),
    load_requests(),
    load_answers(),
)
HR = Path(hr_cli.__file__).parent
START = date(2026, 11, 30)


def _employee(**fields) -> Employee:
    base = EMPLOYEES["e101"].model_dump()
    return Employee.model_validate(base | fields)


def _request(**fields) -> LeaveRequest:
    base = next(r for r in REQUESTS if r.id == "lr_001").model_dump()
    return LeaveRequest.model_validate(base | fields)


def _employer(**fields) -> Employer:
    base = COMPANY.employers[0].model_dump()
    return Employer.model_validate(base | fields)


@pytest.mark.parametrize("request_", REQUESTS, ids=lambda r: r.id)
def test_every_request_gets_the_answer_key_s_result(request_):
    decided = hr_cli.decide(request_, EMPLOYEES, COMPANY)
    assert hr_cli.mismatches(ANSWERS[request_.id], decided) == {}


def test_every_request_has_one_answer_and_every_answer_a_request():
    assert sorted(ANSWERS) == sorted(r.id for r in REQUESTS)
    assert all(r.employee_id in EMPLOYEES for r in REQUESTS)


def test_every_routing_reason_is_in_an_answer_key():
    """Except the pipeline's own, which tests/test_hr_agents.py covers."""
    from hall_monitor.hr.routing import PIPELINE_REASONS

    used = {reason for a in ANSWERS.values() for reason in a.reasons}
    assert used == set(REASONS) - PIPELINE_REASONS


def test_the_cli_scores_every_request(capsys):
    assert hr_cli.main([]) == 0
    assert "28 of 28 match the answer key." in capsys.readouterr().out
    assert hr_cli.main(["--request", "lr_009"]) == 0


# --- Each rule on both sides of its boundary ------------------------------------------------


@pytest.mark.parametrize(
    ("hired", "passed"),
    [(date(2025, 12, 13), True), (date(2025, 12, 14), False), (date(2025, 12, 3), True)],
    ids=["a Saturday puts its week on the payroll", "the next Sunday is a week short", "midweek"],
)
def test_service_is_52_weeks_on_the_payroll(hired, passed):
    employee = _employee(employment=[{"start": hired, "end": None}])
    assert check_service(employee, START).passed is passed


def test_service_that_starts_after_the_leave_doesn_t_count():
    later = _employee(employment=[{"start": date(2026, 12, 7), "end": None}])
    assert check_service(later, START).facts["weeks_counted"] == 0


def test_overlapping_employment_periods_are_refused():
    twice = [{"start": date(2026, 3, 2), "end": None}, {"start": date(2026, 3, 2), "end": None}]
    with pytest.raises(ValueError, match="overlap"):
        _employee(employment=twice)


@pytest.mark.parametrize(
    ("rehired", "counted"),
    [(date(2022, 6, 29), True), (date(2022, 6, 30), False)],
    ids=["a day under 7 years", "exactly 7 years"],
)
def test_service_before_a_break_of_7_years_doesn_t_count(rehired, counted):
    periods = [
        {"start": date(2010, 1, 4), "end": date(2015, 6, 30)},
        {"start": rehired, "end": None},
    ]
    check = check_service(_employee(employment=periods), START)
    assert (check.facts["weeks_before_a_long_break"] == 0) is counted
    kept = check_service(_employee(employment=periods, count_service_before_long_break=True), START)
    assert kept.facts["weeks_before_a_long_break"] == 0


@pytest.mark.parametrize(("last_week", "passed"), [(25, False), (26, True)])
def test_hours_are_1250_in_the_12_months_before(last_week, passed):
    ledger = [
        {
            "weeks_ending_from": date(2025, 11, 29),
            "weeks_ending_to": date(2025, 11, 29),
            "hours_per_week": 100,
        },
        {
            "weeks_ending_from": date(2025, 12, 6),
            "weeks_ending_to": date(2026, 11, 21),
            "hours_per_week": 24,
        },
        {
            "weeks_ending_from": date(2026, 11, 28),
            "weeks_ending_to": date(2026, 11, 28),
            "hours_per_week": last_week,
        },
        {
            "weeks_ending_from": date(2026, 12, 5),
            "weeks_ending_to": date(2026, 12, 5),
            "hours_per_week": 100,
        },
    ]
    check = check_hours(_employee(hours_ledger=ledger), START)
    assert check.passed is passed and check.facts["hours"] == 1224 + last_week


def test_no_hours_record_can_t_be_decided():
    check = check_hours(_employee(hours_ledger=None), START)
    assert check.passed is None and check.code == "facts_missing"


def test_weeks_on_the_payroll_missing_from_the_ledger_aren_t_counted_as_zero():
    """The employer must show hours weren't worked (825.110(c)(3)): a gap is unknown."""
    ledger = [
        {
            "weeks_ending_from": date(2026, 5, 2),
            "weeks_ending_to": date(2026, 11, 28),
            "hours_per_week": 40,
        }
    ]
    gap = check_hours(_employee(hours_ledger=ledger), START)
    assert gap.passed is None and gap.facts["weeks_missing"] == 21
    hired_late = _employee(
        hours_ledger=ledger, employment=[{"start": date(2026, 4, 27), "end": None}]
    )
    assert check_hours(hired_late, START).passed is False  # no weeks before hire are missing


def test_overlapping_hours_entries_are_refused():
    ledger = [
        {
            "weeks_ending_from": date(2026, 1, 3),
            "weeks_ending_to": date(2026, 6, 27),
            "hours_per_week": 40,
        },
        {
            "weeks_ending_from": date(2026, 6, 27),
            "weeks_ending_to": date(2026, 11, 28),
            "hours_per_week": 40,
        },
    ]
    with pytest.raises(ValueError, match="overlap"):
        _employee(hours_ledger=ledger)


@pytest.mark.parametrize(("site", "count"), [("ws-bil", 50), ("ws-lau", 49), ("ws-hq", 175)])
def test_worksite_counts_the_employer_s_sites_within_75_miles_inclusive(site, count):
    check = check_worksite(_employee(worksite_id=site), COMPANY)
    assert check.facts["employees"] == count and check.passed is (count >= 50)


def test_another_employer_s_site_nearby_doesn_t_count():
    check = check_worksite(EMPLOYEES["e108"], COMPANY)
    assert check.facts["sites_within_75_miles"] == ["ws-lab"] and not check.passed


def test_an_unknown_worksite_can_t_be_decided():
    assert check_worksite(_employee(worksite_id="ws-nowhere"), COMPANY).passed is None


def test_a_site_with_no_distance_can_t_be_counted_as_far():
    company = COMPANY.model_copy(
        update={"surface_miles": tuple(p for p in COMPANY.surface_miles if "ws-lau" not in p[:2])}
    )
    check = check_worksite(_employee(worksite_id="ws-lau"), company)
    assert check.passed is None and "ws-bil" in check.facts["sites_without_a_distance"]


@pytest.mark.parametrize(
    ("public", "weeks", "notice", "covered"),
    [
        (False, {2025: 20, 2026: 0}, date(2026, 11, 2), True),
        (False, {2025: 19, 2026: 19}, date(2026, 11, 2), False),
        (False, {2026: 20}, date(2026, 11, 2), True),
        (False, {2026: 5}, date(2026, 11, 2), None),  # 2025 isn't recorded
        (False, {2025: 52, 2026: 44}, date(2027, 1, 4), True),  # judged on 2026 and 2027
        (False, {2025: 52, 2026: 10}, date(2027, 1, 4), None),
        (True, {}, date(2026, 11, 2), True),
    ],
)
def test_employer_coverage(public, weeks, notice, covered):
    employer = _employer(public_agency=public, weeks_with_50_or_more_employees=weeks)
    assert check_employer(employer, notice).passed is covered


def test_an_employee_with_no_employer_record_can_t_be_decided():
    orphan = _employee(employer_id="nobody")
    assessed = assess(_request(), orphan, COMPANY)
    assert assessed.eligible is None
    assert {c.id for c in assessed.checks if c.passed is None} >= {"employer", "entitlement"}


def test_a_request_for_an_employee_with_no_record_goes_to_a_person():
    decided = hr_cli.decide(_request(employee_id="e999"), EMPLOYEES, COMPANY)
    assert decided["route"]["route"] == "hr_review"
    assert decided["route"]["reasons"] == ["facts_missing"]


@pytest.mark.parametrize(
    ("fields", "passed", "code"),
    [
        ({"reason": "care_for_family_member", "family_relationship": "parent"}, True, None),
        (
            {"reason": "care_for_family_member", "family_relationship": "sibling"},
            False,
            "family_member_not_covered",
        ),
        (
            {
                "reason": "care_for_family_member",
                "family_relationship": "child",
                "family_member_under_18": True,
            },
            True,
            None,
        ),
        (
            {
                "reason": "care_for_family_member",
                "family_relationship": "child",
                "family_member_under_18": False,
            },
            None,
            "adult_child",
        ),
        (
            {
                "reason": "care_for_family_member",
                "family_relationship": "child",
                "family_member_under_18": False,
                "incapable_of_self_care": True,
            },
            True,
            None,
        ),
        (
            {
                "reason": "care_for_family_member",
                "family_relationship": "child",
                "family_member_under_18": False,
                "incapable_of_self_care": False,
            },
            False,
            "family_member_not_covered",
        ),
        (
            {"reason": "care_for_family_member", "family_relationship": "child"},
            None,
            "reason_unclear",
        ),
        ({"reason": "military_caregiver", "family_relationship": "next_of_kin"}, True, None),
        (
            {"reason": "qualifying_exigency", "family_relationship": "next_of_kin"},
            False,
            "family_member_not_covered",
        ),
        ({"reason": "placement_bonding"}, None, "reason_unclear"),
        # The 12 months from 2025-12-01 end before the leave starts on 2026-11-30? No: they run
        # to 2026-11-30, the first day that no longer qualifies.
        ({"reason": "placement_bonding", "event_date": date(2025, 12, 1)}, True, None),
        (
            {"reason": "placement_bonding", "event_date": date(2025, 11, 30)},
            False,
            "bonding_window",
        ),
        (
            {"reason": "care_for_family_member", "family_relationship": "other"},
            None,
            "reason_unclear",
        ),
    ],
)
def test_qualifying_reasons(fields, passed, code):
    check = check_reason(_request(**fields))
    assert (check.passed, check.code) == (passed, code)


# --- The leave year, by each method the regulations allow -----------------------------------


def _leave(start, end, reason="own_serious_health_condition"):
    return {"start": start, "end": end, "reason": reason}


@pytest.mark.parametrize(
    ("method", "starts", "taken", "used"),
    [
        # Calendar year: only 2026 counts.
        (
            "calendar_year",
            None,
            [
                _leave(date(2025, 12, 1), date(2025, 12, 28)),
                _leave(date(2026, 1, 5), date(2026, 3, 1)),
            ],
            8,
        ),
        # A July fiscal year: June 2026 is the last year's.
        (
            "fixed_year",
            "07-01",
            [
                _leave(date(2026, 6, 1), date(2026, 6, 28)),
                _leave(date(2026, 8, 3), date(2026, 8, 16)),
            ],
            2,
        ),
        # Forward from first leave: the period that began 2026-01-05 is still open.
        ("forward_from_first_leave", None, [_leave(date(2026, 1, 5), date(2026, 2, 1))], 4),
        # Forward: the period from 2025-06-02 has ended, so the request starts a new one.
        ("forward_from_first_leave", None, [_leave(date(2025, 6, 2), date(2025, 6, 29))], 0),
        # Forward: a leave running past the first period's end starts the next period on its
        # first day after it (2026-06-02), and its 59 workdays from then count.
        (
            "forward_from_first_leave",
            None,
            [
                _leave(date(2025, 6, 2), date(2025, 6, 29)),
                _leave(date(2026, 5, 4), date(2026, 8, 23)),
            ],
            11.8,
        ),
        # Rolling backward: only the workdays from 2025-11-30 (a Sunday) count: 1 to 5 December.
        ("rolling_backward", None, [_leave(date(2025, 11, 10), date(2025, 12, 7))], 1.0),
        # A fixed year that starts on the leave's first day leaves nothing in it.
        ("fixed_year", "11-30", [_leave(date(2026, 11, 2), date(2026, 11, 27))], 0),
        # Leave is counted in workweeks: Monday to Friday for 12 weeks is 12, not 11.71.
        ("rolling_backward", None, [_leave(date(2026, 6, 1), date(2026, 8, 21))], 12),
        # Five single Mondays are one workweek.
        (
            "rolling_backward",
            None,
            [_leave(date(2026, 9, d), date(2026, 9, d)) for d in (7, 14, 21, 28)]
            + [_leave(date(2026, 10, 5), date(2026, 10, 5))],
            1,
        ),
    ],
)
def test_weeks_used_by_leave_year_method(method, starts, taken, used):
    employer = _employer(leave_year={"method": method, "starts": starts})
    weeks, got = entitlement(_request(), _employee(fmla_leave_taken=taken), employer)
    assert (weeks, got) == (12, used)


def test_military_caregiver_leave_is_26_weeks_in_one_period_from_its_first_day():
    taken = [
        _leave(date(2026, 3, 2), date(2026, 5, 24), "military_caregiver"),  # 12 weeks
        _leave(date(2026, 6, 1), date(2026, 6, 14)),  # 2 weeks of other FMLA leave
    ]
    request = _request(reason="military_caregiver", family_relationship="spouse")
    assert entitlement(request, _employee(fmla_leave_taken=taken), _employer()) == (26, 14)


@pytest.mark.parametrize(
    ("first_day", "known"),
    # The period starts on the first workday of caregiver leave: Monday 2025-12-01 is still open
    # on 2026-11-30; Friday 2025-11-28 has ended.
    [(date(2025, 12, 1), True), (date(2025, 11, 28), False)],
    ids=["period still open", "period ended"],
)
def test_earlier_caregiver_leave_in_an_ended_period_can_t_be_decided(first_day, known):
    """26 weeks are per servicemember and per injury, which the records don't say."""
    taken = [_leave(first_day, first_day + timedelta(days=13), "military_caregiver")]
    request = _request(reason="military_caregiver", family_relationship="spouse")
    weeks, used = entitlement(request, _employee(fmla_leave_taken=taken), _employer())
    assert weeks == 26 and (used is not None) is known


@pytest.mark.parametrize(
    ("end", "intermittent", "taken_until", "route"),
    [
        (date(2026, 12, 13), False, None, "specialist_sign_off"),  # 2 asked, 2 left
        (date(2026, 12, 14), False, None, "hr_review"),  # one workday more
        (date(2027, 5, 30), True, date(2026, 8, 21), "hr_review"),  # none left
        (date(2027, 5, 30), True, date(2026, 8, 14), "hr_review"),  # intermittent anyway
    ],
)
def test_the_entitlement_boundary(end, intermittent, taken_until, route):
    taken = [_leave(date(2026, 6, 1), taken_until or date(2026, 8, 7))]  # 10 weeks by default
    employee = _employee(fmla_leave_taken=taken)
    request = _request(end=end, intermittent=intermittent)
    decided = route_leave(assess(request, employee, COMPANY), request, employee, COMPANY)
    assert decided.route == route
    if taken_until == date(2026, 8, 21):
        assert "past_entitlement" in decided.reasons


def test_a_fixed_year_needs_a_real_start_and_only_it_has_one():
    with pytest.raises(ValueError):
        _employer(leave_year={"method": "fixed_year"})
    with pytest.raises(ValueError):
        _employer(leave_year={"method": "calendar_year", "starts": "07-01"})
    for impossible in ("04-31", "02-29", "11-31"):
        with pytest.raises(ValueError):
            _employer(leave_year={"method": "fixed_year", "starts": impossible})


@pytest.mark.parametrize(
    ("notice", "due"),
    [
        (date(2026, 11, 2), date(2026, 11, 9)),
        (date(2026, 10, 30), date(2026, 11, 6)),
        (date(2026, 11, 7), date(2026, 11, 13)),
    ],
)
def test_the_eligibility_notice_is_due_in_5_business_days(notice, due):
    assert notice_due(notice) == due


def test_29_february_becomes_28_february():
    assert years_after(date(2028, 2, 29), -1) == date(2027, 2, 28)


# --- Nothing but a person decides anything but the light path --------------------------------


CLEAN = [r for r in REQUESTS if ANSWERS[r.id].route == "specialist_sign_off"]
FLAGS = [
    ("employee", {"pending_adverse_action": True}, "retaliation_risk", "hr_director"),
    ("employee", {"key_employee": True}, "key_employee", "leave_specialist"),
    ("employee", {"worksite_id": "ws-fre"}, "state_law", "leave_specialist"),
    ("employee", {"hours_ledger": None}, "facts_missing", "leave_specialist"),
    ("request", {"intermittent": True}, "intermittent", "leave_specialist"),
    ("request", {"possible_accommodation": True}, "possible_accommodation", "leave_specialist"),
    ("request", {"reason": "unclear"}, "reason_unclear", "leave_specialist"),
    ("request", {"end": date(2027, 3, 21)}, "past_entitlement", "hr_director"),
]


@pytest.mark.parametrize("request_", CLEAN, ids=lambda r: r.id)
@pytest.mark.parametrize(("where", "change", "code", "role"), FLAGS, ids=[f[2] for f in FLAGS])
def test_any_flag_on_a_clean_request_sends_it_to_hr_review(request_, where, change, code, role):
    employee = EMPLOYEES[request_.employee_id]
    if where == "employee":
        employee = Employee.model_validate(employee.model_dump() | change)
    else:
        request_ = LeaveRequest.model_validate(request_.model_dump() | change)
    if code == "past_entitlement" and request_.reason.value == "military_caregiver":
        request_ = LeaveRequest.model_validate(request_.model_dump() | {"end": date(2027, 6, 6)})
    route = route_leave(assess(request_, employee, COMPANY), request_, employee, COMPANY)
    assert route.route == "hr_review" and code in route.reasons
    assert route.decided_by in {role, "hr_director"}


def test_only_hr_managers_and_directors_may_deny_or_extend():
    may_deny_or_extend = {
        "employer_not_covered",
        "short_service",
        "low_hours",
        "small_worksite",
        "family_member_not_covered",
        "bonding_window",
        "past_entitlement",
        "retaliation_risk",
        # The agentic path routed the request before the rules ran: the most senior role
        # decides, since nobody knows which role the rules would name (ADR 0028).
        "assessment_skipped",
        # Refused at the spending cap before any model call, so the rules never ran (HM-34).
        "spend_refused",
    }
    for code, (_, role) in REASONS.items():
        assert (role.label != "leave_specialist") is (code in may_deny_or_extend), code


# --- What the records hold -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "flagged"),
    [
        ({"reason": "birth_bonding", "event_date": date(2026, 11, 20)}, True),
        ({"reason": "care_for_family_member", "family_relationship": "parent"}, True),
        ({"reason": "military_caregiver", "family_relationship": "spouse"}, True),
        ({"reason": "care_for_family_member", "family_relationship": "spouse"}, False),
        ({}, False),  # own health condition
    ],
)
def test_spouses_at_one_employer_are_flagged_where_leave_may_be_shared(fields, flagged):
    employee = _employee(spouse_employee_id="e116")
    request = _request(**fields)
    route = route_leave(assess(request, employee, COMPANY), request, employee, COMPANY)
    assert ("spouses_same_employer" in route.reasons) is flagged


def test_a_home_state_with_its_own_leave_law_is_flagged():
    employee = _employee(home_state="CA")
    route = route_leave(assess(_request(), employee, COMPANY), _request(), employee, COMPANY)
    assert route.reasons == ["state_law"]


def test_a_check_that_didn_t_pass_reaches_a_person_even_without_a_code(monkeypatch):
    unknown = Check(id="reason", passed=None, rule="r", cite="c", facts={}, code=None)
    monkeypatch.setattr(eligibility, "check_reason", lambda request: unknown)
    employee, request = EMPLOYEES["e101"], _request()
    route = route_leave(assess(request, employee, COMPANY), request, employee, COMPANY)
    assert route.route == "hr_review" and route.reasons == ["facts_missing"]


# --- What the records hold -------------------------------------------------------------------

# Every field the records carry, nested ones too. A new one fails this test, so adding personal
# or health data is a reviewed change. Health information among them, kept coarse: the reason
# category (of a request and of leave already taken), incapable_of_self_care (a disability) and
# possible_accommodation; bonding requests carry the date of the birth or placement.
EMPLOYEE_FIELDS = {
    "id",
    "employer_id",
    "worksite_id",
    "home_state",
    "employment",
    "count_service_before_long_break",
    "hours_ledger",
    "workdays",
    "fmla_leave_taken",
    "key_employee",
    "spouse_employee_id",
    "pending_adverse_action",
}
REQUEST_FIELDS = {
    "id",
    "employee_id",
    "notice_date",
    "start",
    "end",
    "intermittent",
    "reason",
    "family_relationship",
    "family_member_under_18",
    "incapable_of_self_care",
    "event_date",
    "possible_accommodation",
}


def test_the_records_carry_only_the_fields_the_rules_read():
    from hall_monitor.hr.models import EmploymentPeriod, HoursEntry, PastLeave

    assert set(Employee.model_fields) == EMPLOYEE_FIELDS
    assert set(LeaveRequest.model_fields) == REQUEST_FIELDS
    # And the records inside an employee's.
    assert set(EmploymentPeriod.model_fields) == {"start", "end"}
    assert set(HoursEntry.model_fields) == {
        "weeks_ending_from",
        "weeks_ending_to",
        "hours_per_week",
    }
    assert set(PastLeave.model_fields) == {"start", "end", "reason"}


def test_the_check_facts_carry_no_dates_of_birth_or_placement():
    request = next(r for r in REQUESTS if r.id == "lr_016")
    facts = check_reason(request).facts
    assert str(request.event_date) not in str(facts) and request.event_date not in facts.values()


def test_the_hr_rules_call_no_model():
    """The rules and their records; the agents are separate modules (HM-22)."""
    rules = ("models.py", "eligibility.py", "routing.py", "data.py", "__main__.py")
    for file in (HR / name for name in rules):
        imported = {
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(file.read_text()))
            if isinstance(n, ast.Import | ast.ImportFrom)
            for a in (n.names if isinstance(n, ast.Import) else [n])
        }
        assert not any(str(m).startswith(("anthropic", "hall_monitor.agents")) for m in imported)


# --- Re-review of review 8 -------------------------------------------------------------------


def test_other_leave_in_an_open_caregiver_period_counts_towards_the_26():
    """22 workweeks of caregiver leave leave 4 of the 26, whatever the employer's leave year
    says about the other 12 (825.127(e))."""
    taken = [_leave(date(2026, 6, 1), date(2026, 10, 30), "military_caregiver")]
    employer = _employer(leave_year={"method": "calendar_year", "starts": None})
    request = _request(start=date(2027, 1, 4), end=date(2027, 2, 14), notice_date=date(2027, 1, 4))
    weeks, used = entitlement(request, _employee(fmla_leave_taken=taken), employer)
    assert weeks - used == 4  # the 12 weeks are untouched, but only 4 of the 26 are left


def test_caregiver_days_don_t_count_against_the_12_weeks_for_other_reasons():
    taken = [
        _leave(date(2026, 3, 2), date(2026, 7, 19), "military_caregiver"),  # 20 workweeks
    ]
    employee = _employee(fmla_leave_taken=taken)
    request = _request(start=date(2026, 11, 30), end=date(2026, 12, 27))  # 4 weeks
    assessed = assess(request, employee, COMPANY)
    assert assessed.remaining_weeks == 6  # min(12 - 0, 26 - 20)
    assert next(c for c in assessed.checks if c.id == "entitlement").passed is True


def test_a_monday_to_friday_request_is_counted_in_workweeks():
    """56 workdays used leaves 0.8 weeks; a Monday-to-Friday week is 1 workweek, not 5/7."""
    taken = [
        _leave(date(2026, 6, 1), date(2026, 8, 14)),
        _leave(date(2026, 8, 17), date(2026, 8, 17)),
    ]
    employee = _employee(fmla_leave_taken=taken)
    request = _request(end=date(2026, 12, 4))
    route = route_leave(assess(request, employee, COMPANY), request, employee, COMPANY)
    assert route.reasons == ["past_entitlement"]


def test_birth_bonding_before_the_birth_can_t_be_decided_but_placement_can_start_before():
    before = check_reason(_request(reason="birth_bonding", event_date=date(2027, 1, 15)))
    assert (before.passed, before.code) == (None, "before_birth")
    placement = check_reason(_request(reason="placement_bonding", event_date=date(2027, 1, 15)))
    assert placement.passed is True


def test_bonding_from_29_february_runs_to_the_end_of_28_february():
    request = _request(
        reason="birth_bonding",
        event_date=date(2028, 2, 29),
        start=date(2029, 2, 26),
        end=date(2029, 2, 28),
        notice_date=date(2029, 2, 1),
    )
    assert eligibility.bonding_expires(request) == date(2029, 3, 1)


def test_a_missing_home_state_can_t_be_decided():
    employee = _employee(home_state=None)
    route = route_leave(assess(_request(), employee, COMPANY), _request(), employee, COMPANY)
    assert route.reasons == ["facts_missing"]
    lost = _employee(worksite_id="ws-nowhere", home_state="CA")
    route = route_leave(assess(_request(), lost, COMPANY), _request(), lost, COMPANY)
    assert {"facts_missing", "state_law"} <= set(route.reasons)


def test_overlapping_leave_already_taken_is_refused():
    twice = [
        _leave(date(2026, 6, 1), date(2026, 7, 12)),
        _leave(date(2026, 6, 1), date(2026, 7, 12)),
    ]
    with pytest.raises(ValueError, match="overlap"):
        _employee(fmla_leave_taken=twice)


def test_a_period_ending_on_a_sunday_and_a_leave_starting_on_one_add_no_week():
    ended = _employee(
        employment=[
            {"start": date(2024, 1, 7), "end": date(2024, 1, 14)},  # Sunday to Sunday, exclusive
            {"start": date(2025, 12, 7), "end": None},
        ]
    )
    assert check_service(ended, date(2026, 11, 29)).facts["weeks_counted"] == 1 + 51


@pytest.mark.parametrize(
    ("first_day", "used"),
    [(date(2025, 12, 1), 4), (date(2025, 11, 28), 0)],
    ids=["period still open", "period ended the Friday before"],
)
def test_the_forward_period_on_both_sides_of_its_end(first_day, used):
    taken = [_leave(first_day, first_day + timedelta(days=25))]
    employer = _employer(leave_year={"method": "forward_from_first_leave", "starts": None})
    assert entitlement(_request(), _employee(fmla_leave_taken=taken), employer) == (12, used)


@pytest.mark.parametrize(("starts", "used"), [("11-30", 0), ("12-01", 4)])
def test_the_fixed_year_on_both_sides_of_its_first_day(starts, used):
    taken = [_leave(date(2026, 11, 2), date(2026, 11, 27))]
    employer = _employer(leave_year={"method": "fixed_year", "starts": starts})
    assert entitlement(_request(), _employee(fmla_leave_taken=taken), employer) == (12, used)


def test_an_unknown_leave_year_method_can_t_be_decided():
    employer = _employer().model_copy(
        update={"leave_year": _employer().leave_year.model_copy(update={"method": "lunar"})}
    )
    assert entitlement(_request(), _employee(), employer) == (12, None)
