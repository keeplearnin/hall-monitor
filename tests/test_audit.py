"""The hash-chained audit log: append-only, tamper-evident, and what the workflow writes."""

import hashlib
import sqlite3
import threading

import pytest
from langgraph.types import Command

from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.governance.approval import AdjusterDecision
from hall_monitor.governance.audit import GENESIS_HASH, AuditLog, ChainHead, entry_hash
from hall_monitor.models import NextStep
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import open_workflow
from tests.fakes import TEST_REGISTRY_DIR, fake_client, outputs_from_truth, signed_decision

CLAIMS = {c.claim_id: c for c in iter_claims()}
REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
GATEWAY = ToolGateway(REGISTRY)


@pytest.fixture
def log(tmp_path):
    log = AuditLog(tmp_path / "audit.sqlite")
    for n in range(1, 6):
        log.append(f"claim_00{n}", "agent_call", "intake", {"n": n})
    yield log
    log.close()


def _raw(log: AuditLog) -> sqlite3.Connection:
    """A direct connection, as someone with file access would have, triggers dropped."""
    db = sqlite3.connect(log.path, isolation_level=None)
    db.executescript("DROP TRIGGER audit_log_no_update; DROP TRIGGER audit_log_no_delete;")
    return db


def test_a_clean_chain_verifies(log):
    result = log.verify()
    assert result.ok and result.entries == 5 and result.problems == []
    assert result.head == log.head()
    assert log.entries()[0]["prev_hash"] == GENESIS_HASH


def test_updates_and_deletes_are_refused(log):
    db = sqlite3.connect(log.path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE audit_log SET payload = '{}' WHERE seq = 2")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM audit_log WHERE seq = 2")


def test_a_changed_entry_is_detected(log):
    _raw(log).execute("UPDATE audit_log SET payload = '{\"n\":99}' WHERE seq = 3")
    result = log.verify()
    assert not result.ok
    assert "entry 3 was changed after it was written" in result.problems


def test_a_deleted_entry_is_detected(log):
    _raw(log).execute("DELETE FROM audit_log WHERE seq = 3")
    problems = log.verify().problems
    assert "entry 3 is missing (next entry is 4)" in problems
    assert "entry 4 doesn't follow the entry before it" in problems


def test_reordering_is_detected(log):
    db = _raw(log)
    db.execute("UPDATE audit_log SET claim_id = 'swap' WHERE seq = 2")
    db.execute("UPDATE audit_log SET claim_id = 'claim_002' WHERE seq = 4")
    assert not log.verify().ok


def test_a_consistent_rewrite_is_caught_only_by_an_anchor(log):
    anchor = log.head()  # copied somewhere the attacker can't reach
    db = _raw(log)
    db.execute("UPDATE audit_log SET payload = '{\"n\":99}' WHERE seq = 3")
    rows = db.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()
    prev = GENESIS_HASH
    for seq, at, cid, event, actor, payload, _prev, _hash in rows:  # recompute every hash
        digest = entry_hash(seq, at, cid, event, actor, payload, prev)
        db.execute(
            "UPDATE audit_log SET prev_hash = ?, entry_hash = ? WHERE seq = ?", (prev, digest, seq)
        )
        prev = digest
    AuditLog(log.path).close()  # a careful attacker puts the triggers back

    assert log.verify().ok  # internally consistent: the chain alone can't tell
    result = log.verify(expect=anchor)
    assert not result.ok and "anchored head" in result.problems[0]


def test_verify_accepts_a_matching_anchor(log):
    anchor = log.head()
    log.append("claim_006", "agent_call", "intake", {"n": 6})  # the log keeps growing
    assert log.verify(expect=ChainHead(anchor.seq, anchor.entry_hash)).ok


# --- What the workflow writes -----------------------------------------------------------


def _workflow(tmp_path, claim_id=None):
    client = fake_client(outputs_from_truth(load_ground_truth(claim_id))) if claim_id else None
    return open_workflow(
        tmp_path / "checkpoints.sqlite",
        audit_db=tmp_path / "audit.sqlite",
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=load_policy_wording(),
        client=client,
    )


def test_an_automatic_claim_leaves_a_complete_trail(tmp_path):
    with _workflow(tmp_path, "claim_001") as workflow:
        workflow.submit(CLAIMS["claim_001"])
        entries = workflow.audit.entries("claim_001")
        assert workflow.audit.verify().ok

    events = [e["event"] for e in entries]
    assert events[0] == "claim_submitted"
    assert events.count("agent_call") == 5  # intake, coverage, risk, critic, recommendation
    assert "tool_call" in events and "triage_result" in events and "evidence_pack" in events
    assert events[-1] == "final_decision" and entries[-1]["actor"] == "auto"


def test_an_adjuster_decision_and_a_rejection_are_both_recorded(tmp_path):
    with _workflow(tmp_path, "claim_003") as workflow:
        status = workflow.submit(CLAIMS["claim_003"])  # pays 6,480: needs a person
    pay = {
        "next_step": NextStep.PAY,
        "payout_usd": 6480.0,
        "evidence_sha256": status["evidence_sha256"],
    }
    with _workflow(tmp_path) as workflow:
        # adj-101's authority is 5,000: rejected. adj-201 may approve it.
        workflow.decide("claim_003", signed_decision("claim_003", adjuster_id="adj-101", **pay))
        workflow.decide("claim_003", signed_decision("claim_003", adjuster_id="adj-201", **pay))
        entries = workflow.audit.entries("claim_003")
        assert workflow.audit.verify().ok

    rejected = [e for e in entries if e["event"] == "decision_rejected"]
    assert len(rejected) == 1 and rejected[0]["actor"] == "adjuster:adj-101"
    assert "authority" in rejected[0]["payload"]["rejected_because"]
    final = entries[-1]
    assert final["event"] == "final_decision" and final["actor"] == "adjuster:adj-201"
    assert final["payload"]["evidence_sha256"] == status["evidence_sha256"]


def test_the_log_holds_no_claim_text(tmp_path):
    claim = CLAIMS["claim_016"]  # a medical claim with a blood-alcohol reading
    with _workflow(tmp_path, "claim_016") as workflow:
        workflow.submit(claim)
        everything = str(workflow.audit.entries())
    assert claim.narrative[:40] not in everything
    assert "blood alcohol" not in everything


def test_the_log_keeps_the_adjusters_reason_as_a_hash(tmp_path):
    reason = "Crohn's medication changed 43 days before purchase (5.1)."
    with _workflow(tmp_path, "claim_004") as workflow:
        status = workflow.submit(CLAIMS["claim_004"])
        decision = signed_decision(
            "claim_004",
            adjuster_id="adj-101",
            next_step=NextStep.DENY,
            payout_usd=0,
            reason=reason,
            evidence_sha256=status["evidence_sha256"],
        )
        assert workflow.decide("claim_004", decision)["final"]["reason"] == reason
        everything = str(workflow.audit.entries())
        final = workflow.audit.entries("claim_004")[-1]["payload"]
    assert "Crohn" not in everything and "Jordan Avery" not in everything
    assert final["signature"] == decision.signature
    # Keyed, so guessing the reason and hashing it can't confirm it.
    assert "reason_sha256" not in final
    assert final["reason_hmac"] != hashlib.sha256(reason.encode()).hexdigest()
    assert final["reason_hmac"] == workflow.audit.keyed_hash(reason)


def test_evidence_is_stored_by_hash_and_checked_by_verify(tmp_path):
    with _workflow(tmp_path, "claim_004") as workflow:
        status = workflow.submit(CLAIMS["claim_004"])
        digest = status["evidence_sha256"]
        assert workflow.audit.get_evidence(digest) == status["evidence"]
        log = workflow.audit

        db = sqlite3.connect(log.path, isolation_level=None)
        db.execute("DROP TRIGGER evidence_no_update")
        db.execute("UPDATE evidence SET body = replace(body, 'deny', 'pay')")
        result = log.verify()
        assert not result.ok
        assert any("was changed after it was stored" in p for p in result.problems)

        db.execute("DELETE FROM evidence")  # erasure: allowed, reported, not tampering
        db.close()
        AuditLog(log.path).close()  # the dropped trigger is back
        result = log.verify()
        assert result.ok and result.evidence_erased == [digest]


def test_read_only_verify_reports_removed_triggers_and_malformed_entries(log):
    log.append("claim_009", "evidence_pack", "pipeline", {"no": "hash"})
    db = _raw(log)  # drops the triggers
    db.close()
    reader = AuditLog(log.path, read_only=True)
    result = reader.verify()
    reader.close()
    assert any("trigger audit_log_no_update is missing" in p for p in result.problems)
    assert any("evidence_pack entry is malformed" in p for p in result.problems)
    db = sqlite3.connect(log.path)
    triggers = db.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()
    db.close()
    assert triggers[0] == 1  # the read-only open put nothing back (evidence_no_update is untouched)


def test_keyed_hashes_depend_on_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("HALL_MONITOR_AUDIT_KEY", raising=False)
    first = AuditLog(tmp_path / "a" / "audit.sqlite")
    second = AuditLog(tmp_path / "b" / "audit.sqlite")
    key_file = tmp_path / "a" / "audit.key"
    assert key_file.exists() and key_file.stat().st_mode & 0o077 == 0  # owner-only
    assert first.keyed_hash("x") != second.keyed_hash("x")  # each log has its own key
    reopened = AuditLog(tmp_path / "a" / "audit.sqlite")
    assert reopened.keyed_hash("x") == first.keyed_hash("x")  # and keeps it
    monkeypatch.setenv("HALL_MONITOR_AUDIT_KEY", "k" * 64)
    assert AuditLog(tmp_path / "c" / "audit.sqlite").keyed_hash("x") == AuditLog(
        tmp_path / "d" / "audit.sqlite"
    ).keyed_hash("x")  # a key from the environment (a secrets manager) wins
    for log in (first, second, reopened):
        log.close()


def test_reads_and_writes_from_many_threads_stay_consistent(log):
    errors = []

    def write(n):
        try:
            for i in range(50):
                log.append(f"claim_{n}", "agent_call", "intake", {"i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def read():
        try:
            for _ in range(50):
                assert log.verify().ok
                log.entries()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(3)]
    threads += [threading.Thread(target=read) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == [] and log.verify().ok and log.verify().entries == 5 + 150


def test_verify_checks_an_anchor_at_sequence_zero(log):
    from hall_monitor.audit import main

    path = str(log.path)
    assert main(["--db", path, "verify", "--expect-seq", "0", "--expect-hash", "x" * 64]) == 3


# --- From the whole-repo review (Phase B) -------------------------------------------------


def test_a_rejected_decision_logs_no_names_or_submitted_text(tmp_path):
    """Rejections reach the log as `rejected_because`, so they name adjusters by ID only and
    never repeat what was submitted."""
    marker = "Ana Ruiz passport X1234567"
    with _workflow(tmp_path, "claim_003") as workflow:
        status = workflow.submit(CLAIMS["claim_003"])
        digest = status["evidence_sha256"]
        over_authority = signed_decision(
            "claim_003",
            adjuster_id="adj-101",
            next_step=NextStep.PAY,
            payout_usd=6480.0,
            reason="x" * 30,
            evidence_sha256=digest,
        )
        workflow.decide("claim_003", over_authority)
        unregistered = AdjusterDecision(
            adjuster_id=marker,
            next_step=NextStep.PAY,
            payout_usd=10.0,
            reason="x" * 30,
            evidence_sha256=digest,
        )
        workflow.decide("claim_003", unregistered)
        config = {"configurable": {"thread_id": "claim_003"}}
        workflow.app.invoke(
            Command(resume={"adjuster_id": "adj-101", "notes": marker}), config
        )  # a malformed submission: pydantic would echo the value
        rejections = [
            e for e in workflow.audit.entries("claim_003") if e["event"] == "decision_rejected"
        ]
        everything = str(workflow.audit.entries())
    assert len(rejections) == 3
    assert "Jordan Avery" not in everything
    assert "Ana Ruiz" not in everything and "X1234567" not in everything
