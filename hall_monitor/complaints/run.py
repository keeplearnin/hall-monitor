"""Triage complaints into the audit log, for the desk's people to work.

    uv run python -m hall_monitor.complaints.run --demo                  # pinned snapshot, scripted
    uv run python -m hall_monitor.complaints.run --live --limit 20       # calls the Claude API

--demo runs every complaint in the pinned snapshot through the pipeline with the scripted model
(complaints/scripted.py): no API call, no cost, written to the demo audit log. --live calls the
Claude API (Haiku) for each complaint, through the desk's guarded model client (HM-25), up to
--limit (at most 500), from the pinned snapshot or the desk's store (--from-store). At Haiku's
price a complaint costs well under a cent; the run prints its total. A complaint already triaged
is skipped. Nothing is sent to anyone: drafts are kept in the evidence store, never sent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
from hall_monitor.complaints.models import Complaint
from hall_monitor.complaints.pipeline import ROUTED, scope_id, triage_complaint
from hall_monitor.complaints.scripted import scripted_client
from hall_monitor.complaints.store import DEFAULT_STORE, ComplaintStore
from hall_monitor.egress import audit_refusals, model_client
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog
from hall_monitor.registry import Registry

DEMO_AUDIT_DB = DEFAULT_AUDIT_DB.parent / "demo" / "complaints-audit.sqlite"
MAX_LIVE = 500


def run(complaints: list[Complaint], audit: AuditLog, client, registry: Registry) -> dict:
    """Triage each complaint not already in the log; a summary of what happened."""
    summary = {
        "triaged": 0,
        "skipped": 0,
        "specialist_review": 0,
        "standard_queue": 0,
        "agent_failed": 0,
        "cost_usd": 0.0,
    }
    for complaint in complaints:
        if audit.has(scope_id(complaint.complaint_id), ROUTED):
            summary["skipped"] += 1
            continue
        result = triage_complaint(complaint, registry=registry, client=client, audit=audit)
        summary["triaged"] += 1
        summary[result.route.queue] += 1
        summary["agent_failed"] += "agent_failed" in result.route.reasons
        summary["cost_usd"] = round(summary["cost_usd"] + result.cost_usd, 6)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.complaints.run")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="scripted model, no API calls")
    mode.add_argument("--live", action="store_true", help="calls the Claude API")
    parser.add_argument("--limit", type=int, help="most complaints to triage (live: required)")
    parser.add_argument("--snapshot", default=PINNED_NAME)
    parser.add_argument("--from-store", type=Path, nargs="?", const=DEFAULT_STORE)
    parser.add_argument("--audit-db", type=Path)
    args = parser.parse_args(argv)
    if args.live and (args.limit is None or not 1 <= args.limit <= MAX_LIVE):
        parser.error(f"--live needs --limit between 1 and {MAX_LIVE}")
    if args.from_store:
        with ComplaintStore(args.from_store) as store:
            complaints = store.complaints()
    else:
        complaints = load_pinned(args.snapshot)
    complaints = complaints[: args.limit] if args.limit else complaints
    audit = AuditLog(args.audit_db or (DEMO_AUDIT_DB if args.demo else DEFAULT_AUDIT_DB))
    try:
        if args.demo:
            client = scripted_client()
        else:
            load_dotenv()
            client = model_client(on_refused=audit_refusals(audit))
        summary = run(complaints, audit, client, Registry.from_dir())
    finally:
        audit.close()
        tracing.flush()
    summary["mode"] = "demo (scripted model)" if args.demo else "live"
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
