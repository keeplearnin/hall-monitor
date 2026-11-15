"""Score the partner claim rules against the hand-written answer keys (no model, no network).

uv run python -m hall_monitor.partners
uv run python -m hall_monitor.partners --claim C-108
"""

from __future__ import annotations

import argparse

from hall_monitor.partners.data import load_answers, load_book
from hall_monitor.partners.rules import REASONS, assess_all

FIELDS = ("tier", "route", "decided_by", "reasons", "reimbursable_usd", "held_usd")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.partners")
    parser.add_argument("--claim", help="show one claim's assessment")
    args = parser.parse_args(argv)
    results, answers = assess_all(load_book()), load_answers()

    if args.claim:
        if args.claim not in results:
            parser.error(f"no claim {args.claim}")
        result = results[args.claim]
        print(f"{result.claim_id}: {result.tier.value}, {result.route}, by {result.decided_by}")
        print(f"  reimbursable {result.reimbursable_usd} USD, held {result.held_usd} USD")
        for code in result.reasons:
            print(f"  - {code}: {REASONS[code][0]}")
        return 0

    mismatches = 0
    for claim_id in sorted(answers):
        got, want = results[claim_id], answers[claim_id]
        wrong = [
            f
            for f in FIELDS
            if (getattr(got, f).value if f == "tier" else getattr(got, f)) != getattr(want, f)
        ]
        if wrong:
            mismatches += 1
            print(f"{claim_id}: differs on {', '.join(wrong)}")
    matched = len(answers) - mismatches
    print(f"{matched} of {len(answers)} match the answer key.")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
