"""Pull complaints from CFPB into the desk's store, incrementally, with snapshots.

    uv run python -m hall_monitor.complaints.ingest pull                 # one pull, network GETs
    uv run python -m hall_monitor.complaints.ingest pull --every 6h      # on a schedule
    uv run python -m hall_monitor.complaints.ingest replay SNAPSHOT_DIR  # offline, from disk
    uv run python -m hall_monitor.complaints.ingest pin PULL_ID --name 2026-10-01
    uv run python -m hall_monitor.complaints.ingest status

A pull asks the governed tool gateway for pages of complaints (cfpb.py), oldest first, resuming
after the stored cursor, until it has caught up or taken the desk's capacity for one pull. Each
page is written to disk as the tool returned it (already minimised: no company, ZIP prefix only)
before it is processed, with a manifest of hashes, so any pull can be replayed offline and the
pinned snapshot in data/cfpb/snapshots/ reproduces every eval and demo. Each record is validated:
a new one is stored, a repeat is skipped, a changed one is stored as a new version, and one that
fails is quarantined with field names and error types only. The cursor moves only after a page
is processed, so a failure keeps what was done.

The audit log gets each tool call, each quarantined record and one complaint_pull entry per pull:
IDs, hashes, counts and codes. Structured logs (JSON lines) go to stderr. Only GET requests to
the CFPB complaint API leave the machine (HM-25); CI never runs a pull.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import secrets
import shutil
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from hall_monitor.complaints.cfpb import CFPB_SEARCH, UNRECOGNISED_FIELD, complaint_gateway
from hall_monitor.complaints.models import CfpbSource, Complaint
from hall_monitor.complaints.store import DEFAULT_STORE, ComplaintStore
from hall_monitor.egress import CFPB_POLICY, CfpbClient, audit_refusals, guarded_client
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog, TriageRecorder
from hall_monitor.models import ToolCall
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import PIPELINE_ACTOR, ToolGateway

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOTS = ROOT / "var" / "complaints" / "snapshots"
PINNED_SNAPSHOTS = ROOT / "data" / "cfpb" / "snapshots"
# The snapshot every eval and demo runs from, and the hash of each pinned snapshot's manifest:
# a page and its manifest entry edited together still fail against this.
PINNED_NAME = "2026-10-01"
PINNED_MANIFESTS = {
    "2026-10-01": "be882cd30552a7d503acde001b50fea4d8c33dd3e8f0d7ad63a6f04f1f510647",
}
_PULL_ID = re.compile(r"^pull-\d{8}T\d{6}Z-[0-9a-f]{6}$")
DEFAULT_CAPACITY = 200
PAGE_SIZE = 100
MANIFEST = "manifest.json"

log = logging.getLogger("hall_monitor.complaints")


def _event(event: str, **fields) -> None:
    log.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class SnapshotError(ValueError):
    """A snapshot on disk doesn't match its manifest."""


class PullError(RuntimeError):
    """A pull can't go on; the message is a code, never a value from CFPB."""


# --- Processing a page (shared by pull and replay) -----------------------------------------


def _reason(exc: Exception) -> str:
    """Field names and error types only: never a value from the record."""
    if isinstance(exc, ValidationError):
        known = set(CfpbSource.model_fields)

        def where(loc: tuple) -> str:
            # A key from the record is named only if it is one of the model's own fields.
            parts = [str(p) if p in known or isinstance(p, int) else "unknown_field" for p in loc]
            return ".".join(parts) or "record"

        parts = sorted({f"{where(e['loc'])}:{e['type']}" for e in exc.errors()})
        return ", ".join(parts)[:500]
    if str(exc) == "unknown tag":
        return "tags:unknown_tag"
    return f"record:{type(exc).__name__}"


def _process_page(
    page: dict, store: ComplaintStore, pull_id: str, audit: AuditLog | None, counts: dict
) -> str | None:
    """Store, skip or quarantine each record on a page; the cursor after the last one."""
    cursor = None
    for hit in page.get("hits", []):
        hit = hit if isinstance(hit, dict) else {}
        source = hit.get("source")
        counts["fetched"] += 1
        try:
            if source is None:
                raise ValueError("record has no source")
            complaint = Complaint.from_source(source)
        except (ValidationError, ValueError, TypeError) as exc:
            raw_sha256 = _sha256(_canonical(source))
            complaint_id = source.get("complaint_id") if isinstance(source, dict) else None
            complaint_id = (
                complaint_id if isinstance(complaint_id, str) and complaint_id.isdigit() else None
            )
            reason = "source:missing" if source is None else _reason(exc)
            if store.quarantine(pull_id, raw_sha256, complaint_id, reason):
                counts["quarantined"] += 1
                if audit is not None:
                    audit.append(
                        pull_id,
                        "complaint_quarantined",
                        PIPELINE_ACTOR,
                        {"complaint_id": complaint_id, "raw_sha256": raw_sha256, "reason": reason},
                    )
            else:
                counts["quarantined_again"] += 1
        else:
            counts[store.upsert(complaint, pull_id)] += 1
        for name in hit.get("dropped_fields") or []:
            ok = isinstance(name, str) and re.fullmatch(r"[a-z][a-z_]{0,39}", name)
            counts.setdefault("dropped_fields", set()).add(name if ok else UNRECOGNISED_FIELD)
        if isinstance(hit.get("cursor"), str):
            cursor = hit["cursor"]
    return cursor


def _new_counts() -> dict:
    return {
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "quarantined": 0,
        "quarantined_again": 0,
    }


def _public_counts(counts: dict) -> dict:
    return {k: (sorted(v) if isinstance(v, set) else v) for k, v in counts.items()}


# --- Snapshots ------------------------------------------------------------------------------


def _write_page(directory: Path, number: int, page: dict) -> dict:
    body = json.dumps(page, sort_keys=True, indent=1, default=str) + "\n"
    name = f"page-{number:04d}.json"
    (directory / name).write_text(body)
    return {"file": name, "sha256": _sha256(body), "records": len(page.get("hits", []))}


def load_snapshot(directory: Path) -> tuple[dict, list[dict]]:
    """A snapshot's manifest and pages, each checked against the manifest's hashes."""
    manifest_file = directory / MANIFEST
    if not manifest_file.exists():
        raise SnapshotError(f"{directory.name}: no {MANIFEST}")
    manifest = json.loads(manifest_file.read_text())
    pages = []
    for entry in manifest.get("pages", []):
        name = entry.get("file", "")
        if not re.fullmatch(r"page-\d{4}\.json", name):
            raise SnapshotError(f"{directory.name}: bad page name in the manifest")
        body = (directory / name).read_text()
        if _sha256(body) != entry.get("sha256"):
            raise SnapshotError(f"{directory.name}/{name} doesn't match its manifest hash")
        pages.append(json.loads(body))
    return manifest, pages


def _date_min(since: date | None, cursor: str | None, started: datetime) -> date:
    """Where a pull's date filter starts. Resuming, it is the day before the cursor's: CFPB's
    filter may not be in UTC, and search_after skips what was already read."""
    if since is not None:
        return since
    if cursor is not None:
        received = datetime.fromtimestamp(int(cursor.split("_")[0]) / 1000, UTC)
        return received.date() - timedelta(days=1)
    return started.date() - timedelta(days=2)


def _cursor_key(cursor: str) -> tuple[int, int]:
    ms, complaint_id = cursor.split("_")
    return int(ms), int(complaint_id)


def snapshot_sha256(directory: Path) -> str:
    return _sha256((directory / MANIFEST).read_text())


# --- Pull -----------------------------------------------------------------------------------


def pull(
    store: ComplaintStore,
    gateway: ToolGateway,
    *,
    snapshots: Path = DEFAULT_SNAPSHOTS,
    since: date | None = None,
    capacity: int = DEFAULT_CAPACITY,
    audit: AuditLog | None = None,
    client: CfpbClient | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    """One incremental pull; its summary. since restarts from that date, ignoring the cursor.
    Never raises for CFPB's or the network's failures: the summary's status says what happened."""
    started = now()
    pull_id = f"pull-{started:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    directory = snapshots / pull_id
    directory.mkdir(parents=True)
    cursor_before = None if since is not None else store.cursor()
    cursor, counts, pages = cursor_before, _new_counts(), []
    status, error, total, data_status = "caught_up", None, None, None
    requests_before = None if client is None else client.requests
    retries_before = None if client is None else client.retries
    date_min = started.date()
    recorder = TriageRecorder(audit, pull_id) if audit is not None else None
    try:
        date_min = _date_min(since, cursor_before, started)
        _event(
            "pull_started",
            pull_id=pull_id,
            date_min=date_min,
            cursor=cursor_before,
            capacity=capacity,
        )
        while counts["fetched"] < capacity:
            size = min(PAGE_SIZE, capacity - counts["fetched"])
            arguments: dict = {"date_received_min": date_min.isoformat(), "size": size}
            if cursor:
                arguments["search_after"] = cursor
            calls: list[ToolCall] = []
            try:
                page = gateway.call(PIPELINE_ACTOR, CFPB_SEARCH, arguments, calls)
            finally:
                for call in calls:
                    if recorder is not None:
                        recorder("tool_call", call)
            if "error" in page:  # refused by the gateway
                raise PermissionError("the gateway refused the CFPB tool")
            pages.append(_write_page(directory, len(pages) + 1, page))
            total, data_status = page.get("total"), page.get("data_status")
            advanced = _process_page(page, store, pull_id, audit, counts)
            if page.get("hits"):
                # CFPB already ignores frm; if it ignored search_after too, the same page would
                # come back until capacity ran out, looking like a backlog.
                if advanced is None or (
                    cursor is not None and _cursor_key(advanced) <= _cursor_key(cursor)
                ):
                    raise PullError("cursor_did_not_advance")
                cursor = advanced
                store.set_cursor(cursor)
            _event(
                "page_processed",
                pull_id=pull_id,
                page=len(pages),
                records=len(page.get("hits", [])),
            )
            if len(page.get("hits", [])) < size:
                break
        else:
            status = "at_capacity"
    except PullError as exc:
        status, error = "failed", str(exc)
    except Exception as exc:  # CFPB down, refused, a bad response: the type only
        status, error = "failed", type(exc).__name__
    finished = now()
    summary = {
        "pull_id": pull_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "seconds": round((finished - started).total_seconds(), 3),
        "status": status,
        "error": error,
        "date_min": date_min.isoformat(),
        "cursor_before": cursor_before,
        "cursor_after": cursor,
        "capacity": capacity,
        "cfpb_total_since_date_min": total,
        "data_status": data_status,
        "requests": None if client is None else client.requests - requests_before,
        "retries": None if client is None else client.retries - retries_before,
        "pages": pages,
        **_public_counts(counts),
    }
    (directory / MANIFEST).write_text(json.dumps(summary, sort_keys=True, indent=1) + "\n")
    summary["snapshot_sha256"] = snapshot_sha256(directory)
    store.record_pull(summary)
    if audit is not None:
        audit.append(
            pull_id,
            "complaint_pull",
            PIPELINE_ACTOR,
            {k: v for k, v in summary.items() if k != "pages"},
        )
    _event("pull_finished", **{k: v for k, v in summary.items() if k != "pages"})
    return summary


def replay(directory: Path, store: ComplaintStore, audit: AuditLog | None = None) -> dict:
    """Process a snapshot from disk, as the pull did, with no network."""
    manifest, pages = load_snapshot(directory)
    counts = _new_counts()
    for page in pages:
        _process_page(page, store, manifest["pull_id"], audit, counts)
    return {"pull_id": manifest["pull_id"], **_public_counts(counts)}


def load_pinned(name: str, root: Path = PINNED_SNAPSHOTS) -> list[Complaint]:
    """Every complaint in a pinned snapshot, replayed into memory: what evals and demos use."""
    expected = PINNED_MANIFESTS.get(name)
    if expected is not None and snapshot_sha256(root / name) != expected:
        raise SnapshotError(f"{name}: the manifest isn't the pinned one")
    store = ComplaintStore(Path(":memory:"))
    try:
        replay(root / name, store)
        return store.complaints()
    finally:
        store.close()


def pin(
    pull_id: str, name: str, snapshots: Path = DEFAULT_SNAPSHOTS, root: Path = PINNED_SNAPSHOTS
) -> Path:
    """Copy a checked snapshot into data/cfpb/snapshots/<name>, to commit."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise SystemExit("a pinned snapshot's name is letters, digits, dots, dashes")
    if not _PULL_ID.match(pull_id):
        raise SystemExit("not a pull ID")
    source = snapshots / pull_id
    manifest, _ = load_snapshot(source)
    target = root / name
    if target.exists():
        raise SystemExit(f"{target} exists: pinned snapshots are never overwritten")
    target.mkdir(parents=True)
    # Only what the manifest lists, already checked against it.
    for file in [MANIFEST, *(entry["file"] for entry in manifest["pages"])]:
        shutil.copyfile(source / file, target / file)
    return target


# --- CLI ------------------------------------------------------------------------------------


def parse_every(text: str) -> float:
    match = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if not match:
        raise argparse.ArgumentTypeError("use a number and s, m, h or d, like 6h")
    seconds = int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    if seconds < 60:
        raise argparse.ArgumentTypeError("at least 60 seconds between pulls")
    return float(seconds)


def run_schedule(
    once: Callable[[], dict],
    every: float | None,
    runs: int | None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Pull once, or every `every` seconds until `runs` pulls have run (forever if None)."""
    summaries = []
    while True:
        summaries.append(once())
        if every is None or (runs is not None and len(summaries) >= runs):
            return summaries
        sleep(every)


def scheduled_pull(store: ComplaintStore, gateway: ToolGateway, *, since: date | None, **kwargs):
    """One pull per call, for run_schedule. A restart date applies until a pull succeeds;
    later pulls resume from the cursor."""
    pending = [since]

    def once() -> dict:
        summary = pull(store, gateway, since=pending[0], **kwargs)
        if summary["status"] != "failed":
            pending[0] = None
        return summary

    return once


def _desk(audit: AuditLog) -> tuple[ToolGateway, CfpbClient]:
    client = CfpbClient(guarded_client(CFPB_POLICY, on_refused=audit_refusals(audit)))
    return complaint_gateway(Registry.from_dir(), client), client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.complaints.ingest")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pull", help="pull new complaints from CFPB (GET requests only)")
    p.add_argument("--since", type=date.fromisoformat, help="restart from this date")
    p.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    p.add_argument("--every", type=parse_every, help="repeat, e.g. 6h")
    p.add_argument("--runs", type=int, help="stop after this many pulls")
    r = sub.add_parser("replay", help="process a snapshot from disk, offline")
    r.add_argument("snapshot", type=Path)
    n = sub.add_parser("pin", help="copy a snapshot into data/cfpb/snapshots to commit")
    n.add_argument("pull_id")
    n.add_argument("--name", required=True)
    sub.add_parser("status", help="counts, quarantine and recent pulls")
    for s in (p, r, sub.choices["status"]):
        s.add_argument("--store", type=Path, default=DEFAULT_STORE)
    p.add_argument("--snapshots", type=Path, default=DEFAULT_SNAPSHOTS)
    p.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx", "httpcore", "mcp", "fastmcp"):  # their own lines aren't JSON
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.command == "pin":
        print(pin(args.pull_id, args.name))
        return 0
    with ComplaintStore(args.store) as store:
        if args.command == "replay":
            print(json.dumps(replay(args.snapshot, store), indent=2))
            return 0
        if args.command == "status":
            print(
                json.dumps(
                    {
                        "counts": store.counts(),
                        "cursor": store.cursor(),
                        "pulls": [
                            {k: v for k, v in s.items() if k != "pages"} for s in store.pulls()[-5:]
                        ],
                    },
                    indent=2,
                )
            )
            return 0
        audit = AuditLog(args.audit_db)
        try:
            gateway, client = _desk(audit)
            once = scheduled_pull(
                store,
                gateway,
                since=args.since,
                snapshots=args.snapshots,
                capacity=args.capacity,
                audit=audit,
                client=client,
            )
            summaries = run_schedule(once, args.every, args.runs)
        finally:
            audit.close()
    return 1 if summaries[-1]["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
