"""Adjuster review: list claims waiting for a person, show the evidence, record a decision.

    uv run python -m hall_monitor.review list
    uv run python -m hall_monitor.review show claim_004
    uv run python -m hall_monitor.review decide claim_004 --adjuster adj-101 \\
        --next-step deny --reason "Crohn's medication changed 43 days before purchase (5.1)." \\
        --evidence-sha256 <hash from show>
    uv run python -m hall_monitor.review decide claim_003 --adjuster adj-201 --accept \\
        --evidence-sha256 <hash from show>

Deciding needs the evidence hash that `show` prints, so the record proves which evidence the
adjuster reviewed. Only stored state is read, so no model calls are made.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor.data import load_policy_wording
from hall_monitor.governance.approval import AdjusterDecision
from hall_monitor.models import NextStep
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import DEFAULT_CHECKPOINTS, open_workflow


def _print(data: dict) -> None:
    print(json.dumps(data, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hall_monitor.review", description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoints", type=Path, default=DEFAULT_CHECKPOINTS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Claims waiting for an adjuster.")
    show = commands.add_parser("show", help="A claim's status and evidence pack.")
    show.add_argument("claim_id")
    decide = commands.add_parser("decide", help="Record an adjuster's decision.")
    decide.add_argument("claim_id")
    decide.add_argument("--adjuster", required=True)
    decide.add_argument("--evidence-sha256", required=True)
    choice = decide.add_mutually_exclusive_group(required=True)
    choice.add_argument("--accept", action="store_true", help="Accept the AI's recommendation.")
    choice.add_argument("--next-step", choices=[s.value for s in NextStep])
    decide.add_argument("--payout", type=float, default=0.0)
    decide.add_argument("--reason", default="")
    args = parser.parse_args(argv)

    load_dotenv()
    registry = Registry.from_dir()
    with open_workflow(
        args.checkpoints,
        registry=registry,
        gateway=ToolGateway(registry),
        wording=load_policy_wording(),
    ) as workflow:
        if args.command == "list":
            _print({"waiting_for_adjuster": workflow.waiting()})
            return 0
        status = workflow.status(args.claim_id)
        if args.command == "show":
            _print(status)
            return 0 if status["state"] != "unknown" else 1

        if status["state"] != "waiting_for_adjuster":
            print(
                f"{args.claim_id} is {status['state']}, not waiting for an adjuster",
                file=sys.stderr,
            )
            return 1
        if args.accept:
            evidence = status["evidence"]
            next_step = NextStep(evidence["recommendation"]["next_step"])
            payout = evidence["coverage"]["covered_amount"] if next_step is NextStep.PAY else 0.0
        else:
            next_step, payout = NextStep(args.next_step), args.payout
        decision = AdjusterDecision(
            adjuster_id=args.adjuster,
            next_step=next_step,
            payout_usd=payout,
            reason=args.reason,
            evidence_sha256=args.evidence_sha256,
        )
        result = workflow.decide(args.claim_id, decision)
        _print(
            result
            if result["state"] == "decided"
            else {
                "claim_id": args.claim_id,
                "state": result["state"],
                "decision_rejected": result.get("previous_decision_rejected"),
            }
        )
        return 0 if result["state"] == "decided" else 2


if __name__ == "__main__":
    sys.exit(main())
