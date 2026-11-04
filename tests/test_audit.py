"""The hash-chained audit log: append-only, tamper-evident, and what the workflow writes."""

import sqlite3

import pytest

from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.governance.audit import GENESIS_HASH, AuditLog, ChainHead, entry_hash
from hall_monitor.models import NextStep
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import open_workflow
from tests.fakes import fake_client, outputs_from_truth, signed_decision

CLAIMS = {c.claim_id: c for c in iter_claims()}
REGISTRY = Registry.from_dir()
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
    assert len(final["reason_sha256"]) == 64 and final["signature"] == decision.signature


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
        assert not result.ok and "was changed after it was stored" in result.problems[0]

        db.execute("DELETE FROM evidence")  # erasure: allowed, reported, not tampering
        result = log.verify()
        assert result.ok and result.evidence_erased == [digest]
        db.close()
