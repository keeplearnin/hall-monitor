"""Run a claim through the pipeline.

    uv run python -m hall_monitor.run --claim data/synthetic/claims/claim_001.json

Milestone 1 runs the intake agent only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.agents.intake import run_intake
from hall_monitor.data import load_claim
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, Registry, RegistryError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hall_monitor.run", description=__doc__.split("\n")[0])
    parser.add_argument("--claim", type=Path, required=True, help="Path to a claim JSON file.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_DIR)
    args = parser.parse_args(argv)

    load_dotenv()
    registry = Registry.from_dir(args.registry)
    claim = load_claim(args.claim)

    try:
        extraction = run_intake(claim, registry)
    except RegistryError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        tracing.flush()

    manifest = registry.get("intake")
    output = {
        "claim_id": claim.claim_id,
        "hall_pass": {
            "agent_id": manifest.id,
            "version": manifest.version,
            "model": manifest.model,
            "manifest_digest": manifest.digest(),
        },
        "extraction": extraction.model_dump(mode="json"),
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
