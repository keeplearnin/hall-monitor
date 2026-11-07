"""Score the leave agents against the answer keys. Calls the Claude API: a live run costs money.

    uv run python -m hall_monitor.hr.evals --out evals/results/hr-intake   # 28 requests, < $0.10

Each synthetic message goes through the whole pipeline (intake agent, the rules in code, the
explanation agent). Intake is scored field by field against the structured request the message
describes (data/synthetic/hr/requests.json); the end-to-end result against the answer key
(ground_truth.json). An intake error the rules can't see (a wrong date, the wrong family member)
shows up here, not in routing: that is what this eval is for. No result has been published yet.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.evals.meta import run_meta
from hall_monitor.hr import __main__ as rules
from hall_monitor.hr.data import (
    load_answers,
    load_company,
    load_employees,
    load_messages,
    load_requests,
)
from hall_monitor.hr.models import LeaveIntake
from hall_monitor.hr.pipeline import LeavePreparation, prepare
from hall_monitor.registry import Registry

INTAKE_FIELDS = tuple(LeaveIntake.model_fields)


def score(prepared: LeavePreparation, expected_request, answer) -> dict:
    """Which intake fields match, and whether the end-to-end result matches the answer key."""
    if prepared.intake is None:
        return {"error": prepared.stopped_reason, "fields": {}, "route_matches": False}
    got = prepared.intake.model_dump(mode="json")
    want = expected_request.model_dump(mode="json")
    fields = {f: got[f] == want[f] for f in INTAKE_FIELDS}
    decided = {
        "assessment": None
        if prepared.assessment is None
        else prepared.assessment.model_dump(mode="json"),
        "route": prepared.route.model_dump(),
    }
    diff = rules.mismatches(answer, decided) if prepared.assessment is not None else {"all": True}
    return {
        "error": prepared.stopped_reason,
        "fields": fields,
        "route_matches": not diff,
        "explanation_kept": prepared.explanation is not None,
    }


def run(registry: Registry, client=None) -> dict:
    company, employees = load_company(), load_employees()
    requests = {r.id: r for r in load_requests()}
    answers = load_answers()
    runs = []
    for message in load_messages():
        prepared = prepare(
            message, employees=employees, company=company, registry=registry, client=client
        )
        runs.append(
            {
                "request_id": message.id,
                "score": score(prepared, requests[message.id], answers[message.id]),
                "explanation_problem": prepared.explanation_problem,
                "cost_usd": prepared.cost_usd,
            }
        )
    total = len(runs)
    field_hits = sum(sum(r["score"]["fields"].values()) for r in runs)
    summary = {
        "requests": total,
        "intake_field_accuracy": round(field_hits / (total * len(INTAKE_FIELDS)), 4),
        "intake_exact": sum(
            bool(r["score"]["fields"]) and all(r["score"]["fields"].values()) for r in runs
        ),
        "route_accuracy": round(sum(r["score"]["route_matches"] for r in runs) / total, 4),
        "explanations_kept": sum(bool(r["score"].get("explanation_kept")) for r in runs),
        # The note is advisory, so a failure doesn't stop the request; it is counted here.
        "explanation_failures": sum(bool(r["explanation_problem"]) for r in runs),
        "errors": sum(bool(r["score"]["error"]) for r in runs),
        "cost_usd": round(sum(r["cost_usd"] for r in runs), 4),
    }
    return {"summary": summary, "runs": runs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.hr.evals")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    load_dotenv()
    registry = Registry.from_dir()
    try:
        result = run(registry)
    finally:
        tracing.flush()
    result["meta"] = run_meta(registry, ["leave_intake", "leave_explainer"])
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
