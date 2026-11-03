"""Triage a claim: intake, coverage check, risk and fraud, recommendation, routing.

uv run python -m hall_monitor.run --claim data/synthetic/claims/claim_001.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.data import load_claim, load_policy_records, load_policy_wording
from hall_monitor.pipeline import triage
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, Registry, RegistryError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hall_monitor.run", description=__doc__.split("\n")[0])
    parser.add_argument("--claim", type=Path, required=True, help="Path to a claim JSON file.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_DIR)
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        result = triage(
            load_claim(args.claim),
            registry=Registry.from_dir(args.registry),
            policies=load_policy_records(),
            wording=load_policy_wording(),
        )
    except RegistryError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        tracing.flush()

    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
