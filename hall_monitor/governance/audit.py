"""Tamper-evident audit log: append-only, hash-chained, in SQLite.

Each entry's hash covers its own fields and the previous entry's hash, so changing, deleting
or reordering any entry breaks every hash after it, and `verify` reports where. Database
triggers refuse UPDATE and DELETE, so nothing is changed by accident.

What this does not do on its own: someone with write access to the file can drop the triggers
and rewrite the whole chain consistently. The defence is to copy the chain head (`head`) to
somewhere they can't change, and verify against that copy (`verify(expect=...)`). Entries hold
IDs, hashes and decisions, never claim text, so no personal data goes into a log that can't
be edited.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

GENESIS_HASH = "0" * 64
DEFAULT_AUDIT_DB = Path(__file__).resolve().parents[2] / "var" / "audit.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def entry_hash(
    seq: int, recorded_at: str, claim_id: str, event: str, actor: str, payload: str, prev: str
) -> str:
    return sha256(
        canonical(
            {
                "seq": seq,
                "recorded_at": recorded_at,
                "claim_id": claim_id,
                "event": event,
                "actor": actor,
                "payload": payload,
                "prev_hash": prev,
            }
        )
    )


@dataclass(frozen=True)
class ChainHead:
    seq: int
    entry_hash: str


@dataclass
class Verification:
    ok: bool
    entries: int
    head: ChainHead | None
    problems: list[str] = field(default_factory=list)


class AuditLog:
    """One writer at a time (a lock), any number of readers."""

    def __init__(self, path: Path = DEFAULT_AUDIT_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def append(self, claim_id: str, event: str, actor: str, payload: dict) -> ChainHead:
        body = canonical(payload)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS_HASH)
                recorded_at = datetime.now(UTC).isoformat(timespec="microseconds")
                digest = entry_hash(seq, recorded_at, claim_id, event, actor, body, prev)
                self._db.execute(
                    "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (seq, recorded_at, claim_id, event, actor, body, prev, digest),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return ChainHead(seq, digest)

    def head(self) -> ChainHead | None:
        row = self._db.execute(
            "SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return ChainHead(*row) if row else None

    def entries(self, claim_id: str | None = None) -> list[dict]:
        query = "SELECT seq, recorded_at, claim_id, event, actor, payload, prev_hash, entry_hash"
        query += " FROM audit_log" + (" WHERE claim_id = ?" if claim_id else "") + " ORDER BY seq"
        rows = self._db.execute(query, (claim_id,) if claim_id else ()).fetchall()
        keys = (
            "seq",
            "recorded_at",
            "claim_id",
            "event",
            "actor",
            "payload",
            "prev_hash",
            "entry_hash",
        )
        return [dict(zip(keys, r, strict=True)) | {"payload": json.loads(r[5])} for r in rows]

    def verify(self, expect: ChainHead | None = None) -> Verification:
        """Recompute the chain. `expect` is a head copied out earlier: it must still be in it."""
        rows = self._db.execute(
            "SELECT seq, recorded_at, claim_id, event, actor, payload, prev_hash, entry_hash"
            " FROM audit_log ORDER BY seq"
        ).fetchall()
        problems: list[str] = []
        prev, expected_seq = GENESIS_HASH, 1
        hashes: dict[int, str] = {}
        for seq, recorded_at, claim_id, event, actor, payload, prev_hash, digest in rows:
            if seq != expected_seq:
                problems.append(f"entry {expected_seq} is missing (next entry is {seq})")
            if prev_hash != prev:
                problems.append(f"entry {seq} doesn't follow the entry before it")
            if entry_hash(seq, recorded_at, claim_id, event, actor, payload, prev_hash) != digest:
                problems.append(f"entry {seq} was changed after it was written")
            hashes[seq] = digest
            prev, expected_seq = digest, seq + 1
        if expect is not None and hashes.get(expect.seq) != expect.entry_hash:
            problems.append(
                f"the anchored head (entry {expect.seq}, {expect.entry_hash[:12]}) is not in the "
                "chain: it was rewritten or truncated"
            )
        head = ChainHead(rows[-1][0], rows[-1][7]) if rows else None
        return Verification(ok=not problems, entries=len(rows), head=head, problems=problems)


def record_triage(log: AuditLog, claim, result) -> None:
    """Write a triaged claim's events. Within one triage, events are grouped by kind."""
    cid = result.claim_id
    log.append(
        cid,
        "claim_submitted",
        f"channel:{claim.channel}",
        {
            "claim_sha256": sha256(claim.model_dump_json()),
            "submitted_at": claim.submitted_at.isoformat(),
            "attachments": len(claim.attachments),
            "identifiers_redacted": result.redactions,
            "injection_signals": result.injection_signals,
        },
    )
    for call in result.agent_calls:
        log.append(cid, "agent_call", call.agent_id, call.model_dump(mode="json"))
    for tool in result.tool_calls:
        log.append(cid, "tool_call", tool.actor, tool.model_dump(mode="json"))
    for n, review in enumerate(result.critic_reviews, start=1):
        log.append(
            cid,
            "critic_review",
            "critic",
            {
                "review": n,
                # Issue text can quote the claim, so only its hash is kept here.
                "issues": [
                    {"stage": i.stage.value, "problem_sha256": sha256(i.problem + i.evidence)}
                    for i in review.issues
                ],
            },
        )
    for record in result.revision_history:
        log.append(
            cid,
            "revision",
            record.stage.value,
            {
                "round": record.round,
                "changed": {
                    f: [record.before.get(f), record.after.get(f)] for f in record.changed_fields
                },
            },
        )
    for correction in result.code_corrections:
        log.append(cid, "code_correction", "pipeline", {"correction": correction})
    log.append(
        cid,
        "triage_result",
        "pipeline",
        {
            "result_sha256": sha256(result.model_dump_json()),
            "route": result.routing.route.value,
            "routing_reasons": result.routing.reasons,
            "recommended_next_step": result.recommendation.next_step.value,
            "covered_amount": result.coverage.covered_amount if result.coverage else None,
            "stopped_reason": result.stopped_reason,
            "cost_usd": result.cost_usd,
            "models": sorted({f"{c.agent_id}:{c.model}" for c in result.agent_calls}),
        },
    )
