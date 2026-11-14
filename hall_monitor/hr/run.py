"""Prepare synthetic leave requests into the audit log, for HR to decide. Calls the Claude API.

    uv run python -m hall_monitor.hr.run lr_001 lr_009      # two requests, about $0.002
    uv run python -m hall_monitor.hr.run --all              # all 28, under $0.10

Each message in data/synthetic/hr/messages.json goes through the leave agents and the rules
(hr/pipeline.py) and leaves its evidence pack in the audit log; `python -m hall_monitor.hr.review`
then lists the requests waiting for HR. A request already prepared is skipped and the rest go
on. One whose intake agent failed before the rules ran (an outage, a stopped agent) leaves no
evidence pack, so running it again prepares it once the cause is fixed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog
from hall_monitor.hr.data import load_company, load_employees, load_messages
from hall_monitor.hr.pipeline import prepare
from hall_monitor.registry import Registry
from hall_monitor.spend import DEFAULT_SPEND_DB, SpendLedger


def run(
    request_ids: list[str], audit_db: Path, client=None, spend: SpendLedger | None = None
) -> list[dict]:
    """Prepare each request into the log; the route each got. Unknown IDs are refused."""
    messages = {m.id: m for m in load_messages()}
    unknown = [r for r in request_ids if r not in messages]
    if unknown:
        raise SystemExit(f"no synthetic message for {', '.join(unknown)}")
    company, employees, registry = load_company(), load_employees(), Registry.from_dir()
    audit = AuditLog(audit_db)
    results = []
    try:
        for request_id in request_ids:
            if audit.has(request_id, "leave_evidence"):
                results.append({"request_id": request_id, "skipped": "already prepared"})
                continue
            prepared = prepare(
                messages[request_id],
                employees=employees,
                company=company,
                registry=registry,
                client=client,
                audit=audit,
                spend=spend,
            )
            results.append(
                {
                    "request_id": request_id,
                    "route": prepared.route.route,
                    "decided_by": prepared.route.decided_by,
                    "reasons": prepared.route.reasons,
                    "stopped_reason": prepared.stopped_reason,
                    "cost_usd": prepared.cost_usd,
                }
            )
    finally:
        audit.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.hr.run")
    parser.add_argument("request_ids", nargs="*")
    parser.add_argument("--all", action="store_true", help="every synthetic message")
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    args = parser.parse_args(argv)
    ids = [m.id for m in load_messages()] if args.all else args.request_ids
    if not ids:
        parser.error("name request IDs or pass --all")
    load_dotenv()
    try:
        # It calls the Claude API, so it is held to the leave caps (HM-34).
        results = run(ids, args.audit_db, spend=SpendLedger(DEFAULT_SPEND_DB))
    except ValueError as exc:  # prepared by another run at the same moment
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        tracing.flush()
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
