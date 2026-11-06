"""Check leave requests against the FMLA rules and route them. No model calls.

uv run python -m hall_monitor.hr                 # every synthetic request, scored
uv run python -m hall_monitor.hr --request lr_009  # one request: every check and its facts
"""

from __future__ import annotations

import argparse
import json
import sys

from hall_monitor.hr.data import load_answers, load_company, load_employees, load_requests
from hall_monitor.hr.eligibility import assess
from hall_monitor.hr.models import LeaveAnswer
from hall_monitor.hr.routing import route_leave


def decide(request, employees, company) -> dict:
    """The assessment and route for one request, as plain data. A request for an employee the
    records don't hold goes to a person, as any missing fact does."""
    employee = employees.get(request.employee_id)
    if employee is None:
        return {
            "assessment": None,
            "route": {
                "route": "hr_review",
                "decided_by": "leave_specialist",
                "reasons": ["facts_missing"],
                "explained": ["no employee record for this request"],
            },
        }
    assessment = assess(request, employee, company)
    route = route_leave(assessment, request, employee, company)
    return {"assessment": assessment.model_dump(mode="json"), "route": route.model_dump()}


def as_answered(decided: dict) -> dict:
    """The fields an answer key holds, from a decision."""
    a, r = decided["assessment"], decided["route"]
    if a is None:
        return {"route": r["route"], "decided_by": r["decided_by"], "reasons": r["reasons"]}
    return {
        "eligible": a["eligible"],
        "failed_checks": sorted(c["id"] for c in a["checks"] if c["passed"] is False),
        "unknown_checks": sorted(c["id"] for c in a["checks"] if c["passed"] is None),
        "remaining_weeks": a["remaining_weeks"],
        "requested_weeks": a["requested_weeks"],
        "route": r["route"],
        "decided_by": r["decided_by"],
        "reasons": r["reasons"],
        "notice_due": a["notice_due"],
    }


def mismatches(answer: LeaveAnswer, decided: dict) -> dict:
    expected = answer.model_dump(mode="json", exclude={"request_id", "why"})
    got = as_answered(decided)
    return {k: {"expected": v, "got": got.get(k)} for k, v in expected.items() if got.get(k) != v}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.hr")
    parser.add_argument("--request", help="show one request's checks, facts and route")
    args = parser.parse_args(argv)
    company, employees, answers = load_company(), load_employees(), load_answers()
    requests = load_requests()
    if args.request:
        found = [r for r in requests if r.id == args.request]
        if not found:
            print(f"no request {args.request}", file=sys.stderr)
            return 1
        print(json.dumps(decide(found[0], employees, company), indent=2))
        return 0
    wrong = 0
    for request in requests:
        decided = decide(request, employees, company)
        if request.id not in answers:
            wrong += 1
            print(f"DIFF {request.id}  no answer key")
            continue
        diff = mismatches(answers[request.id], decided)
        wrong += bool(diff)
        route = decided["route"]
        mark = "ok  " if not diff else "DIFF"
        print(
            f"{mark} {request.id}  {route['route']:<20} {route['decided_by']:<17} "
            f"{', '.join(route['reasons']) or '-'}"
        )
        for field, values in diff.items():
            print(f"       {field}: expected {values['expected']}, got {values['got']}")
    print(f"{len(requests) - wrong} of {len(requests)} match the answer key.")
    return 0 if not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
