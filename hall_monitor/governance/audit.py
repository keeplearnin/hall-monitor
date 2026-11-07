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

import functools
import hashlib
import hmac
import json
import os
import secrets
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
CREATE TABLE IF NOT EXISTS evidence (
    sha256 TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is content-addressed and never changes'); END;
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
    # Evidence packs the log refers to that are no longer stored (erased, for example under a
    # data-protection request). Not tampering: their hashes are still in the chain.
    evidence_erased: list[str] = field(default_factory=list)


class AlreadyRecorded(RuntimeError):
    """An event that may happen once per claim (a final decision) is already in the log."""


class EvidenceChanged(RuntimeError):
    pass


def evidence_body(pack: dict) -> str:
    """The stored form of an evidence pack; its SHA-256 is the pack's hash
    (governance.approval.evidence_sha256)."""
    return json.dumps(pack, sort_keys=True)


TRIGGERS = ("audit_log_no_update", "audit_log_no_delete", "evidence_no_update")
# Entries that point at a stored evidence pack: a claim's, and a leave request's (hr/signoff.py).
EVIDENCE_EVENTS = ("evidence_pack", "leave_evidence")


def _evidence_ref(payload: str) -> str | None:
    """The evidence hash an evidence entry refers to, or None if the entry is malformed."""
    try:
        digest = json.loads(payload)["evidence_sha256"]
    except (ValueError, KeyError, TypeError):
        return None
    return digest if isinstance(digest, str) else None


KEY_ENV = "HALL_MONITOR_AUDIT_KEY"


def _load_key(path: Path, create: bool) -> bytes | None:
    """The key for keyed hashes: from the environment (a secrets manager in production), else
    a file beside the log, created owner-only on first use."""
    if os.getenv(KEY_ENV):
        return os.environ[KEY_ENV].encode()
    key_file = path.parent / "audit.key"
    if key_file.exists():
        return key_file.read_bytes()
    if not create:
        return None
    key = secrets.token_hex(32).encode()
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _locked(method):
    """Run a method under the log's lock: the connection is shared by threads, and a read
    must never see another thread's half-written entry."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class AuditLog:
    """One connection shared by threads; every read and write takes the same lock.

    read_only=True opens the file without creating or changing anything, for inspection: verify
    then also reports triggers someone removed, which a normal open would quietly put back.

    Free text that may hold personal data (an adjuster's reason, a critic's issue) is stored as
    a keyed hash (HMAC-SHA256), so it can be matched by someone holding the key but not
    confirmed by guessing the text and hashing it. (A final decision's signature, which covers
    the reason, is logged too and can confirm a guess with the public key: HM-06 gap.)
    """

    def __init__(self, path: Path = DEFAULT_AUDIT_DB, *, read_only: bool = False):
        self.path = path
        self._lock = threading.RLock()
        if read_only:
            self._db = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                check_same_thread=False,
                isolation_level=None,
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._db.executescript(_SCHEMA)
        self._key = _load_key(path, create=not read_only)

    def keyed_hash(self, text: str) -> str:
        if self._key is None:
            raise RuntimeError(f"no audit key available: set {KEY_ENV}")
        return hmac.new(self._key, text.encode(), hashlib.sha256).hexdigest()

    def close(self) -> None:
        self._db.close()

    def append(
        self, claim_id: str, event: str, actor: str, payload: dict, *, once: bool = False
    ) -> ChainHead:
        """Add an entry. With once, refuse (AlreadyRecorded) if the claim already has this
        event, checked inside the same write transaction, so two processes deciding the same
        claim at the same moment can't both record a decision."""
        body = canonical(payload)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if once and self._first(claim_id, event) is not None:
                    raise AlreadyRecorded(f"{claim_id} already has a {event} in the audit log")
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

    def put_evidence(self, pack: dict) -> str:
        """Store an evidence pack under its own hash and return the hash. The log entry that
        refers to it carries the same hash, so a changed pack can't go unnoticed; deleting one
        (erasure) is allowed and shows up in verify as erased, not as tampering."""
        body = evidence_body(pack)
        digest = sha256(body)
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?)", (digest, body))
        return digest

    @_locked
    def get_evidence(self, digest: str) -> dict | None:
        """The stored pack, or None if it isn't stored. Raises EvidenceChanged if it no longer
        matches its hash."""
        row = self._db.execute("SELECT body FROM evidence WHERE sha256 = ?", (digest,)).fetchone()
        if row is None:
            return None
        if sha256(row[0]) != digest:
            raise EvidenceChanged(f"evidence pack {digest[:12]} was changed after it was stored")
        return json.loads(row[0])

    def _first(self, claim_id: str, event: str) -> tuple[int, str] | None:
        return self._db.execute(
            "SELECT seq, payload FROM audit_log WHERE claim_id = ? AND event = ?"
            " ORDER BY seq LIMIT 1",
            (claim_id, event),
        ).fetchone()

    @_locked
    def has(self, claim_id: str, event: str) -> bool:
        return self._first(claim_id, event) is not None

    @_locked
    def final_decision(self, claim_id: str) -> dict | None:
        """The final decision the log recorded for a claim (there is at most one)."""
        row = self._first(claim_id, "final_decision")
        return json.loads(row[1]) if row else None

    def _evidence_entry(self, claim_id: str) -> tuple[int, str] | None:
        rows = self._db.execute(
            "SELECT seq, payload FROM audit_log WHERE claim_id = ? AND event = 'evidence_pack'"
            " ORDER BY seq LIMIT 2",
            (claim_id,),
        ).fetchall()
        if len(rows) > 1:
            # Triage records at most one pack per claim (the append is once-only), so a second
            # was appended by something else: trust neither.
            raise EvidenceChanged(f"more than one evidence pack is recorded for {claim_id}")
        return rows[0] if rows else None

    @_locked
    def evidence_hash(self, claim_id: str, *, strict: bool = True) -> str | None:
        """The hash of the evidence pack recorded at the claim's triage. Raises EvidenceChanged
        if more than one is recorded: an entry appended later can't replace it. strict=False
        returns the first one regardless, for showing an adjuster what triage stored."""
        if not strict:
            row = self._first(claim_id, "evidence_pack")
            return _evidence_ref(row[1]) if row else None
        row = self._evidence_entry(claim_id)
        return _evidence_ref(row[1]) if row else None

    @_locked
    def result_hash(self, claim_id: str) -> str | None:
        """The hash of the triage result the claim's evidence pack was built from: recorded in
        the pack's entry, or, in a log written before it was, the latest result logged before
        the pack (a restarted triage records another). Never one appended after."""
        pack = self._evidence_entry(claim_id)
        if pack is not None and (recorded := json.loads(pack[1]).get("result_sha256")):
            return recorded
        row = self._db.execute(
            "SELECT payload FROM audit_log WHERE claim_id = ? AND event = 'triage_result'"
            " AND seq < ? ORDER BY seq DESC LIMIT 1",
            (claim_id, pack[0] if pack else 2**63 - 1),
        ).fetchone()
        return json.loads(row[0]).get("result_sha256") if row else None

    @_locked
    def head(self) -> ChainHead | None:
        row = self._db.execute(
            "SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return ChainHead(*row) if row else None

    @_locked
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

    @_locked
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
        present = {
            name
            for (name,) in self._db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        problems += [
            f"trigger {name} is missing: entries could have been changed in place"
            for name in TRIGGERS
            if name not in present
        ]
        erased = []
        for seq, _, _, event, _, payload, _, _ in rows:
            if event not in EVIDENCE_EVENTS:
                continue
            digest = _evidence_ref(payload)
            if digest is None:
                problems.append(f"entry {seq}: {event} entry is malformed")
                continue
            try:
                if self.get_evidence(digest) is None:
                    erased.append(digest)
            except EvidenceChanged as exc:
                problems.append(f"entry {seq}: {exc}")
        head = ChainHead(rows[-1][0], rows[-1][7]) if rows else None
        return Verification(
            ok=not problems,
            entries=len(rows),
            head=head,
            problems=problems,
            evidence_erased=erased,
        )


class TriageRecorder:
    """Writes one claim's triage events to the log as they happen (pipeline.triage's on_event).

    Payloads hold IDs, hashes, decision fields and costs, never claim text: critic issues and
    the claim itself are hashed.
    """

    def __init__(self, log: AuditLog, claim_id: str):
        self.log, self.claim_id = log, claim_id

    def __call__(self, event: str, item) -> None:
        actor, payload = getattr(self, f"_{event}")(item)
        self.log.append(self.claim_id, event, actor, payload)

    @staticmethod
    def _claim_submitted(item: dict) -> tuple[str, dict]:
        claim = item["claim"]
        return f"channel:{claim.channel}", {
            "claim_sha256": sha256(claim.model_dump_json()),
            "submitted_at": claim.submitted_at.isoformat(),
            "attachments": len(claim.attachments),
            "identifiers_redacted": item["redactions"],
            "injection_signals": item["injection_signals"],
        }

    @staticmethod
    def _agent_call(call) -> tuple[str, dict]:
        return call.agent_id, call.model_dump(mode="json")

    @staticmethod
    def _tool_call(tool) -> tuple[str, dict]:
        return tool.actor, tool.model_dump(mode="json")

    def _critic_review(self, review) -> tuple[str, dict]:
        # Issue text can quote the claim, so only its keyed hash is kept here.
        return "critic", {
            "issues": [
                {
                    "stage": i.stage.value,
                    "problem_hmac": self.log.keyed_hash(i.problem + i.evidence),
                }
                for i in review.issues
            ]
        }

    @staticmethod
    def _revision(record) -> tuple[str, dict]:
        return record.stage.value, {
            "round": record.round,
            "changed": {
                f: [record.before.get(f), record.after.get(f)] for f in record.changed_fields
            },
        }

    @staticmethod
    def _code_correction(correction: str) -> tuple[str, dict]:
        return "pipeline", {"correction": correction}

    @staticmethod
    def _triage_result(result) -> tuple[str, dict]:
        return "pipeline", {
            "result_sha256": sha256(result.model_dump_json()),
            "route": result.routing.route.value,
            "routing_reasons": result.routing.reasons,
            "recommended_next_step": result.recommendation.next_step.value,
            "covered_amount": result.coverage.covered_amount if result.coverage else None,
            "stopped_reason": result.stopped_reason,
            "cost_usd": result.cost_usd,
            "models": sorted({f"{c.agent_id}:{c.model}" for c in result.agent_calls}),
        }
