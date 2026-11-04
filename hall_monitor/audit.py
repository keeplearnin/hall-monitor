"""Inspect and verify the audit log. Reads only; nothing here can change the log.

uv run python -m hall_monitor.audit verify
uv run python -m hall_monitor.audit head            # copy this somewhere safe
uv run python -m hall_monitor.audit verify --expect-seq 42 --expect-hash <hash>
uv run python -m hall_monitor.audit show claim_004
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog, ChainHead


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hall_monitor.audit", description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_AUDIT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="Recompute the chain and report any tampering.")
    verify.add_argument("--expect-seq", type=int, help="Sequence number of an anchored head.")
    verify.add_argument("--expect-hash", help="Entry hash of that anchored head.")
    commands.add_parser("head", help="The latest entry's sequence number and hash.")
    show = commands.add_parser("show", help="Every entry for one claim.")
    show.add_argument("claim_id")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"no audit log at {args.db}", file=sys.stderr)
        return 1
    log = AuditLog(args.db, read_only=True)
    try:
        if args.command == "head":
            head = log.head()
            print(json.dumps({"seq": head.seq, "entry_hash": head.entry_hash} if head else None))
            return 0
        if args.command == "show":
            print(json.dumps(log.entries(args.claim_id), indent=2))
            return 0
        if (args.expect_seq is None) != (args.expect_hash is None):
            parser.error("--expect-seq and --expect-hash go together")
        expect = ChainHead(args.expect_seq, args.expect_hash) if args.expect_seq else None
        result = log.verify(expect)
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "entries": result.entries,
                    "head": {"seq": result.head.seq, "entry_hash": result.head.entry_hash}
                    if result.head
                    else None,
                    "problems": result.problems,
                    "evidence_erased": result.evidence_erased,
                },
                indent=2,
            )
        )
        return 0 if result.ok else 3
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
