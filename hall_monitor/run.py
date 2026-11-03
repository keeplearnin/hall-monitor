"""Submit a claim: triage it, then decide it automatically or send it to an adjuster.

    uv run python -m hall_monitor.run --claim data/synthetic/claims/claim_001.json

A claim the routing rule sends to a person waits in the workflow (state saved in
var/checkpoints.sqlite) until an adjuster decides it with `python -m hall_monitor.review`.
`--triage-only` prints the pipeline's full result without the workflow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.data import load_claim, load_policy_wording
from hall_monitor.pipeline import triage
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import DEFAULT_CHECKPOINTS, open_workflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hall_monitor.run", description=__doc__.split("\n")[0])
    parser.add_argument("--claim", type=Path, required=True, help="Path to a claim JSON file.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_DIR)
    parser.add_argument("--checkpoints", type=Path, default=DEFAULT_CHECKPOINTS)
    parser.add_argument("--no-critic", action="store_true", help="Skip the critic review loop.")
    parser.add_argument(
        "--model-routing",
        action="store_true",
        help="Use approved economy models for small claims (off by default; not yet evaluated).",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="Discard this claim's saved workflow and start again."
    )
    parser.add_argument(
        "--triage-only", action="store_true", help="Print the pipeline result; no workflow."
    )
    args = parser.parse_args(argv)

    load_dotenv()
    registry = Registry.from_dir(args.registry)
    gateway = ToolGateway(registry)
    claim = load_claim(args.claim)
    try:
        if args.triage_only:
            result = triage(
                claim,
                registry=registry,
                gateway=gateway,
                wording=load_policy_wording(),
                critic=not args.no_critic,
                model_routing=args.model_routing,
            )
            print(result.model_dump_json(indent=2))
            return 0
        with open_workflow(
            args.checkpoints,
            registry=registry,
            gateway=gateway,
            wording=load_policy_wording(),
            critic=not args.no_critic,
            model_routing=args.model_routing,
        ) as workflow:
            if args.fresh:
                workflow.saver.delete_thread(claim.claim_id)
            status = workflow.submit(claim)
    finally:
        tracing.flush()

    print(json.dumps(status, indent=2))
    if status["state"] == "waiting_for_adjuster":
        print(
            f"\n{claim.claim_id} is waiting for an adjuster. Review it with:\n"
            f"  uv run python -m hall_monitor.review show {claim.claim_id}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
