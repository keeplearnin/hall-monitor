"""The HR sign-off, from the command line. No model calls.

    uv run python -m hall_monitor.hr.review list
    uv run python -m hall_monitor.hr.review show lr_009
    uv run python -m hall_monitor.hr.review decide lr_009 --staff hrs-301 --outcome approve \\
        --weeks 2 --reason "Two weeks left; the rest goes through company policy" \\
        --evidence-sha256 <hash from show>

`show` prints the evidence pack and its hash; the decision quotes the hash back and is signed
with the staff member's key (--key, or their public demo key for the fictional staff).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog, EvidenceChanged
from hall_monitor.governance.signing import demo_private_key, load_private_key
from hall_monitor.hr.signoff import (
    DecisionRefused,
    LeaveDecision,
    Outcome,
    decide,
    evidence,
    load_staff,
    status,
)


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _waiting(audit: AuditLog) -> list[str]:
    ids = sorted({e["claim_id"] for e in audit.entries() if e["event"] == "leave_evidence"})
    return [i for i in ids if status(audit, i)["state"] == "waiting for HR"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.hr.review")
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="requests waiting for HR")
    show = commands.add_parser("show", help="a request's state, evidence pack and its hash")
    show.add_argument("request_id")
    cmd = commands.add_parser("decide", help="sign and record a decision")
    cmd.add_argument("request_id")
    cmd.add_argument("--staff", required=True)
    cmd.add_argument("--outcome", required=True, choices=[o.value for o in Outcome])
    cmd.add_argument("--weeks", type=float, default=0)
    cmd.add_argument("--reason", default="")
    cmd.add_argument("--evidence-sha256", required=True)
    cmd.add_argument("--key", type=Path, help="the staff member's Ed25519 private key (PEM)")
    args = parser.parse_args(argv)

    if not args.audit_db.exists():
        print(f"no audit log at {args.audit_db}", file=sys.stderr)
        return 1
    audit = AuditLog(args.audit_db)
    try:
        if args.command == "list":
            _print({"waiting_for_hr": _waiting(audit)})
            return 0
        if args.command == "show":
            state = status(audit, args.request_id)
            if state["state"] == "unknown":
                print(f"no request {args.request_id} in the audit log", file=sys.stderr)
                return 1
            found = evidence(audit, args.request_id)
            if found is not None:
                state |= {"evidence_sha256": found[0], "evidence": found[1]}
            _print(state)
            return 0
        staff = load_staff()
        member = staff.get(args.staff)
        if args.key is not None:
            key = load_private_key(args.key)
        elif member is not None and member.demo_key:
            print(f"warning: signing with {args.staff}'s public demo key", file=sys.stderr)
            key = demo_private_key(args.staff)
        else:
            print(f"--key is required to sign as {args.staff}", file=sys.stderr)
            return 1
        decision = LeaveDecision(
            staff_id=args.staff,
            outcome=Outcome(args.outcome),
            weeks=args.weeks,
            reason=args.reason,
            evidence_sha256=args.evidence_sha256,
        ).signed(args.request_id, key)
        try:
            _print(decide(audit, args.request_id, decision, staff))
        except DecisionRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        return 0
    except (EvidenceChanged, DecisionRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        audit.close()


if __name__ == "__main__":
    sys.exit(main())
