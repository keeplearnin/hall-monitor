"""Simulated human override rate, from a saved eval run. No model calls.

    uv run python -m hall_monitor.evals.override evals/results/hardened-critic

The simulated adjuster decides every claim the routing rule sends to a person exactly as the
answer key does. An override is a routed claim where that decision differs from the AI's
recommendation (next step, or payout on a payment). Also counted: AI errors on claims routed
automatically, which no person saw. Real adjusters disagree with the answer key and with each
other, so this is a lower bound on the work the gate creates, not a forecast.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hall_monitor.data import load_ground_truth
from hall_monitor.models import NextStep, Route


def ai_matches_key(result: dict, truth) -> bool:
    if result["recommendation"]["next_step"] != truth.next_step.value:
        return False
    if truth.next_step is NextStep.PAY:
        coverage = result["coverage"]
        return (
            coverage is not None
            and abs(coverage["covered_amount"] - truth.coverage.covered_amount) <= 1.0
        )
    return True


def simulate(run_dir: Path) -> dict:
    data = json.loads((run_dir / "results.json").read_text())
    routed = overrides = auto = auto_errors = 0
    examples: list[str] = []
    for run in data["runs"]:
        result = run["result"]
        if result is None or run["score"]["error"]:
            continue
        truth = load_ground_truth(result["claim_id"])
        correct = ai_matches_key(result, truth)
        if result["routing"]["route"] == Route.ADJUSTER.value:
            routed += 1
            if not correct:
                overrides += 1
                examples.append(
                    f"{result['claim_id']} trial {run['score']['trial']}: AI "
                    f"{result['recommendation']['next_step']}, adjuster {truth.next_step.value}"
                )
        else:
            auto += 1
            auto_errors += not correct
    return {
        "run": run_dir.name,
        "git_commit": data["meta"]["git_commit"],
        "routed_to_adjuster": routed,
        "overrides": overrides,
        "human_override_rate": overrides / routed if routed else None,
        "routed_automatically": auto,
        "unreviewed_ai_errors": auto_errors,
        "override_examples": examples,
    }


def main(argv: list[str] | None = None) -> int:
    run_dir = Path((argv or sys.argv[1:])[0])
    summary = simulate(run_dir)
    (run_dir / "override.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
