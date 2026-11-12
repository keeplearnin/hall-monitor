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
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
from hall_monitor.complaints.models import Complaint
from hall_monitor.complaints.pipeline import (
    RECEIVED,
    ROUTED,
    AlreadyTriaged,
    route_unfinished,
    scope_id,
    triage_complaint,
)
from hall_monitor.complaints.scripted import scripted_client
from hall_monitor.complaints.store import DEFAULT_STORE, ComplaintStore
from hall_monitor.egress import audit_refusals, model_client
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AlreadyRecorded, AuditLog
from hall_monitor.registry import Registry

DEMO_AUDIT_DB = DEFAULT_AUDIT_DB.parent / "demo" / "complaints-audit.sqlite"
MAX_LIVE = 500


def run(complaints: list[Complaint], audit: AuditLog, client, registry: Registry) -> dict:
    """Triage each complaint not already in the log; a summary of what happened. A complaint a
    killed run claimed but never routed goes to specialist review now (interrupted)."""
    summary = {
        "triaged": 0,
        "skipped": 0,
        "interrupted": 0,
        "specialist_review": 0,
        "standard_queue": 0,
        "agent_failed": 0,
        "supervisor_failed": 0,
        "cost_usd": 0.0,
    }
    for complaint in complaints:
        scope = scope_id(complaint.complaint_id)
        if audit.has(scope, RECEIVED):
            if not audit.has(scope, ROUTED):
                # A run still working on it at this moment loses its route to this one.
                try:
                    route_unfinished(complaint, audit)
                    summary["interrupted"] += 1
                    summary["specialist_review"] += 1
                    continue
                except AlreadyRecorded:
                    pass
            summary["skipped"] += 1
            continue
        try:
            result = triage_complaint(complaint, registry=registry, client=client, audit=audit)
        except AlreadyTriaged:  # another run took it between the check and the write
            summary["skipped"] += 1
            continue
        summary["triaged"] += 1
        summary[result.route.queue] += 1
        summary["agent_failed"] += "agent_failed" in result.route.reasons
        summary["supervisor_failed"] += "supervisor_failed" in result.route.reasons
        summary["cost_usd"] = round(summary["cost_usd"] + result.cost_usd, 6)
    return summary


def _same_file(a: Path, b: Path) -> bool:
    """The same path, or the same file by another name (a hard or symbolic link)."""
    if a.resolve() == b.resolve():
        return True
    return a.exists() and b.exists() and os.path.samefile(a, b)


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
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.demo and args.audit_db is not None and _same_file(args.audit_db, DEFAULT_AUDIT_DB):
        parser.error("--demo never writes to the main audit log: scripted answers stay apart")
    if args.from_store:
        with ComplaintStore(args.from_store) as store:
            complaints = store.complaints()
    else:
        complaints = load_pinned(args.snapshot)
    complaints = complaints[: args.limit] if args.limit is not None else complaints
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
