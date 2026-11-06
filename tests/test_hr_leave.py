"""HM-21: leave eligibility, entitlement and routing are decided by code, on the boundaries."""

import ast
from datetime import date
from pathlib import Path

import pytest

from hall_monitor.hr import __main__ as hr_cli
from hall_monitor.hr.data import load_answers, load_company, load_employees, load_requests
from hall_monitor.hr.eligibility import (
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
    used = {reason for a in ANSWERS.values() for reason in a.reasons}
    assert used == set(REASONS)


def test_the_cli_scores_every_request(capsys):
    assert hr_cli.main([]) == 0
    assert "25 of 25 match the answer key." in capsys.readouterr().out
    assert hr_cli.main(["--request", "lr_009"]) == 0


# --- Each rule on both sides of its boundary ------------------------------------------------


@pytest.mark.parametrize(
    ("hired", "passed"), [(date(2025, 12, 1), True), (date(2025, 12, 2), False)]
)
def test_service_is_52_weeks(hired, passed):
    employee = _employee(employment=[{"start": hired, "end": None}])
    assert check_service(employee, START).passed is passed


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
    assert (check.facts["days_before_a_long_break"] == 0) is counted
    kept = check_service(_employee(employment=periods, count_service_before_long_break=True), START)
    assert kept.facts["days_before_a_long_break"] == 0


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


@pytest.mark.parametrize(
    ("public", "weeks", "covered"),
    [
        (False, {2025: 20, 2026: 0}, True),
        (False, {2025: 19, 2026: 19}, False),
        (False, {2026: 20}, True),
        (True, {}, True),
    ],
)
def test_employer_coverage(public, weeks, covered):
    employer = _employer(public_agency=public, weeks_with_50_or_more_employees=weeks)
    assert check_employer(employer, date(2026, 11, 2)).passed is covered


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
                "family_member_age": 17,
            },
            True,
            None,
        ),
        (
            {
                "reason": "care_for_family_member",
                "family_relationship": "child",
                "family_member_age": 18,
            },
            None,
            "adult_child",
        ),
        (
            {
                "reason": "care_for_family_member",
                "family_relationship": "child",
                "family_member_age": 30,
                "incapable_of_self_care": True,
            },
            True,
            None,
        ),
        (
            {
                "reason": "care_for_family_member",
                "family_relationship": "child",
                "family_member_age": 30,
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
        ({"reason": "placement_bonding", "event_date": date(2025, 12, 28)}, True, None),
        (
            {"reason": "placement_bonding", "event_date": date(2025, 12, 27)},
            False,
            "bonding_window",
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
        # Rolling backward: only the 8 days from 2025-11-30 count.
        ("rolling_backward", None, [_leave(date(2025, 11, 10), date(2025, 12, 7))], 1.14),
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


def test_a_fixed_year_needs_its_start_and_only_it_has_one():
    with pytest.raises(ValueError):
        _employer(leave_year={"method": "fixed_year"})
    with pytest.raises(ValueError):
        _employer(leave_year={"method": "calendar_year", "starts": "07-01"})


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
    }
    for code, (_, role) in REASONS.items():
        assert (role.label != "leave_specialist") is (code in may_deny_or_extend), code


# --- What the records hold -------------------------------------------------------------------


def test_the_records_hold_no_names_and_nothing_medical():
    for model in (Employee, LeaveRequest):
        fields = set(model.model_fields)
        assert not fields & {
            "name",
            "first_name",
            "last_name",
            "diagnosis",
            "condition",
            "certification",
            "medical_notes",
        }, model
    assert all("name" not in e.model_dump() for e in EMPLOYEES.values())


def test_the_hr_rules_call_no_model():
    for file in HR.glob("*.py"):
        imported = {
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(file.read_text()))
            if isinstance(n, ast.Import | ast.ImportFrom)
            for a in (n.names if isinstance(n, ast.Import) else [n])
        }
        assert not any(str(m).startswith(("anthropic", "hall_monitor.agents")) for m in imported)
