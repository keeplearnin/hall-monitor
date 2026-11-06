"""The adjuster approval gate and its saved checkpoints (fake Claude client, no network)."""

import pytest

from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.governance.approval import AdjusterDecision, build_evidence_pack, evidence_sha256
from hall_monitor.governance.signing import demo_private_key
from hall_monitor.models import NextStep
from hall_monitor.pipeline import triage
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import open_workflow
from tests.fakes import TEST_REGISTRY_DIR, fake_client, outputs_from_truth, signed_decision

CLAIMS = {c.claim_id: c for c in iter_claims()}
REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
GATEWAY = ToolGateway(REGISTRY)
WORDING = load_policy_wording()


def _open(path, claim_id=None):
    client = fake_client(outputs_from_truth(load_ground_truth(claim_id))) if claim_id else None
    return open_workflow(
        path,
        audit_db=path.with_name("audit.sqlite"),
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=client,
    )


def _submit(path, claim_id):
    with _open(path, claim_id) as workflow:
        return workflow.submit(CLAIMS[claim_id])


def _decide(path, claim_id, **fields):
    with _open(path) as workflow:  # a fresh process: nothing but the saved checkpoint
        status = workflow.status(claim_id)
        fields.setdefault("evidence_sha256", status["evidence_sha256"])
        return workflow.decide(claim_id, signed_decision(claim_id, **fields))


@pytest.fixture
def db(tmp_path):
    return tmp_path / "checkpoints.sqlite"


def test_small_clean_claim_is_decided_automatically(db):
    status = _submit(db, "claim_001")
    assert status["state"] == "decided"
    final = status["final"]
    assert final["decided_by"] == "auto" and final["payout_usd"] == 1992.0
    assert len(final["evidence_sha256"]) == 64


def test_claim_waits_for_an_adjuster_across_a_restart(db):
    status = _submit(db, "claim_004")  # a denial
    assert status["state"] == "waiting_for_adjuster"
    assert "nothing covered (a denial)" in status["routing_reasons"]

    with _open(db) as workflow:  # reopened: the claim is still waiting
        assert workflow.waiting() == ["claim_004"]

    status = _decide(
        db,
        "claim_004",
        adjuster_id="adj-101",
        next_step=NextStep.DENY,
        payout_usd=0,
        reason="Medication changed 43 days before purchase: pre-existing (5.1).",
    )
    final = status["final"]
    assert status["state"] == "decided"
    assert final["decided_by"] == "adjuster:adj-101" and final["adjuster_name"] == "Jordan Avery"
    assert final["overrode_ai"] is False
    with _open(db) as workflow:
        assert workflow.waiting() == []


def test_payout_above_authority_needs_a_senior_adjuster(db):
    _submit(db, "claim_003")  # pay 6,480
    accept = {"next_step": NextStep.PAY, "payout_usd": 6480.0}

    status = _decide(db, "claim_003", adjuster_id="adj-101", **accept)
    assert status["state"] == "waiting_for_adjuster"
    assert "authority" in status["previous_decision_rejected"]

    status = _decide(db, "claim_003", adjuster_id="adj-201", **accept)
    assert status["state"] == "decided"
    assert status["final"]["decided_by"] == "adjuster:adj-201"


def test_paying_a_flagged_claim_needs_a_senior_adjuster(db):
    _submit(db, "claim_006")  # pay 100, duplicate receipt flagged
    pay = {"next_step": NextStep.PAY, "payout_usd": 100.0}
    status = _decide(db, "claim_006", adjuster_id="adj-101", **pay)
    assert "senior adjuster" in status["previous_decision_rejected"]
    assert _decide(db, "claim_006", adjuster_id="adj-201", **pay)["state"] == "decided"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"adjuster_id": "adj-999"}, "isn't registered"),
        ({"evidence_sha256": "0" * 64}, "evidence hash"),
        ({"next_step": NextStep.PAY, "payout_usd": 50.0}, "reason"),  # override, no reason
        ({"next_step": NextStep.PAY, "payout_usd": 99_999.0, "reason": "x" * 30}, "exceeds"),
        ({"next_step": NextStep.DENY, "payout_usd": 10.0, "reason": "x" * 30}, "payout"),
    ],
)
def test_decisions_that_break_the_rules_are_rejected(db, fields, message):
    _submit(db, "claim_004")
    decision = {"adjuster_id": "adj-201", "next_step": NextStep.DENY, "payout_usd": 0} | fields
    decision.setdefault("reason", "")
    status = _decide(db, "claim_004", **decision)
    assert status["state"] == "waiting_for_adjuster"
    assert message in status["previous_decision_rejected"]


def test_an_override_with_a_reason_is_recorded_as_one(db):
    _submit(db, "claim_004")
    status = _decide(
        db,
        "claim_004",
        adjuster_id="adj-201",
        next_step=NextStep.PAY,
        payout_usd=500.0,
        reason="Goodwill payment approved by claims manager for the lodge deposit.",
    )
    final = status["final"]
    assert final["overrode_ai"] is True and final["ai_next_step"] == "deny"


def test_evidence_hash_is_stable_and_tracks_content():
    claim = CLAIMS["claim_004"]
    result = triage(
        claim,
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=fake_client(outputs_from_truth(load_ground_truth("claim_004"))),
    )
    pack = build_evidence_pack(result, claim)
    assert evidence_sha256(pack) == evidence_sha256(build_evidence_pack(result, claim))
    pack["coverage"]["covered_amount"] = 1.0
    assert evidence_sha256(pack) != evidence_sha256(build_evidence_pack(result, claim))


def test_a_claim_can_only_be_submitted_once(db):
    _submit(db, "claim_001")
    with pytest.raises(ValueError, match="already"):
        _submit(db, "claim_001")


# --- Signatures and evidence integrity ---------------------------------------------------

DENY = {
    "adjuster_id": "adj-101",
    "next_step": NextStep.DENY,
    "payout_usd": 0,
    "reason": "Medication changed 43 days before purchase: pre-existing (5.1).",
}


@pytest.mark.parametrize(
    "make",
    [
        lambda h: AdjusterDecision(**DENY, evidence_sha256=h),
        lambda h: AdjusterDecision(**DENY, evidence_sha256=h).signed(
            "claim_004", demo_private_key("adj-201")
        ),
        lambda h: AdjusterDecision(**DENY, evidence_sha256=h).signed(
            "claim_003", demo_private_key("adj-101")
        ),
        lambda h: (
            AdjusterDecision(**DENY, evidence_sha256=h)
            .signed("claim_004", demo_private_key("adj-101"))
            .model_copy(update={"reason": "Changed after it was signed, by someone else."})
        ),
    ],
    ids=["unsigned", "someone-elses-key", "signed-for-another-claim", "changed-after-signing"],
)
def test_a_decision_counts_only_with_the_adjusters_signature(db, make):
    _submit(db, "claim_004")
    with _open(db) as workflow:
        digest = workflow.status("claim_004")["evidence_sha256"]
        status = workflow.decide("claim_004", make(digest))
    assert status["state"] == "waiting_for_adjuster"
    assert "isn't signed with adj-101's key" in status["previous_decision_rejected"]


@pytest.mark.parametrize("rehash", [False, True], ids=["pack-edited", "pack-and-hash-edited"])
def test_an_edited_checkpoint_cant_change_the_evidence(db, rehash):
    _submit(db, "claim_004")
    with _open(db) as workflow:
        config = {"configurable": {"thread_id": "claim_004"}}
        pack = workflow.status("claim_004")["evidence"]
        pack["recommendation"]["next_step"] = "pay"  # someone with file access edits it
        update = {"evidence": pack}
        if rehash:
            update["evidence_sha256"] = evidence_sha256(pack)
        workflow.app.update_state(config, update)
        digest = workflow.status("claim_004")["evidence_sha256"]
        decision = AdjusterDecision(**DENY, evidence_sha256=digest)
        status = workflow.decide(
            "claim_004", decision.signed("claim_004", demo_private_key("adj-101"))
        )
    assert status["state"] == "waiting_for_adjuster"
    assert "evidence pack" in status["previous_decision_rejected"]


def test_a_claim_whose_triage_crashed_can_be_restarted_on_request(db, monkeypatch):
    import hall_monitor.workflow as workflow_module

    real = workflow_module.build_evidence_pack

    def crash(*args):
        raise RuntimeError("process died mid-triage")

    monkeypatch.setattr(workflow_module, "build_evidence_pack", crash)
    with pytest.raises(RuntimeError):
        _submit(db, "claim_001")
    with _open(db) as workflow:
        assert workflow.status("claim_001")["state"] == "in_progress"

    monkeypatch.setattr(workflow_module, "build_evidence_pack", real)
    # Not restarted silently: another process might still be triaging it.
    with pytest.raises(ValueError, match="restart"):
        _submit(db, "claim_001")
    with _open(db, "claim_001") as workflow:
        assert workflow.submit(CLAIMS["claim_001"], restart=True)["state"] == "decided"


def test_the_adjuster_is_shown_the_stored_evidence_not_an_edited_checkpoint(db):
    _submit(db, "claim_004")
    with _open(db) as workflow:
        stored = workflow.status("claim_004")["evidence"]
        edited = stored | {"recommendation": {"next_step": "pay", "reasons": []}}
        workflow.app.update_state(
            {"configurable": {"thread_id": "claim_004"}}, {"evidence": edited}
        )
        status = workflow.status("claim_004")
    assert status["evidence"] == stored
    assert "changed after triage" in status["evidence_problem"]


# --- From the whole-repo review (Phase B) -------------------------------------------------


def _run_fresh(db, claim_id, monkeypatch):
    """`run --fresh` as a user runs it, but on the fake client and this test's databases."""
    import hall_monitor.run as run_module
    from hall_monitor.data import DATA_DIR

    real = run_module.open_workflow

    def fake_open(checkpoints, **kwargs):
        client = fake_client(outputs_from_truth(load_ground_truth(claim_id)))
        return real(checkpoints, audit_db=db.with_name("audit.sqlite"), client=client, **kwargs)

    monkeypatch.setattr(run_module, "open_workflow", fake_open)
    claim_file = DATA_DIR / "claims" / f"{claim_id}.json"
    return run_module.main(["--claim", str(claim_file), "--checkpoints", str(db), "--fresh"])


@pytest.mark.parametrize("claim_id", ["claim_001", "claim_004"], ids=["decided", "waiting"])
def test_run_fresh_refuses_a_claim_that_finished_triage(db, monkeypatch, claim_id):
    """--fresh restarts a crashed triage only: a decided claim must not be paid twice, and a
    claim waiting for an adjuster must not be re-run until it routes automatically."""
    _submit(db, claim_id)
    with pytest.raises(ValueError, match="already"):
        _run_fresh(db, claim_id, monkeypatch)
    with _open(db) as workflow:
        triaged = [e for e in workflow.audit.entries(claim_id) if e["event"] == "triage_result"]
    assert len(triaged) == 1


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r["risk"].update(flags=[], level="low"),
        lambda r: r["extraction"].update(total_claimed=1.0),
    ],
    ids=["fraud-flag-cleared", "amount-lowered"],
)
def test_an_edited_checkpoint_result_cant_relax_the_approval_rules(db, edit):
    """The approval rules read the saved triage result, so it must be the one the log recorded."""
    _submit(db, "claim_006")  # pay 100, duplicate receipt flagged: needs a senior adjuster
    with _open(db) as workflow:
        config = {"configurable": {"thread_id": "claim_006"}}
        result = workflow.app.get_state(config).values["result"]
        edit(result)
        workflow.app.update_state(config, {"result": result})
    status = _decide(
        db, "claim_006", adjuster_id="adj-101", next_step=NextStep.PAY, payout_usd=100.0
    )
    assert status["state"] == "waiting_for_adjuster"
    assert "triage result" in status["previous_decision_rejected"]


def test_a_claim_the_log_shows_triaged_cant_be_triaged_again_with_new_checkpoints(db):
    """The audit log, not the checkpoint file, decides whether a claim finished triage: a new
    or deleted checkpoints file must not let a decided claim be paid again."""
    _submit(db, "claim_001")
    other = db.with_name("other-checkpoints.sqlite")  # same audit log, fresh checkpoints
    with pytest.raises(ValueError, match="audit log"):
        _submit(other, "claim_001")
    with _open(db) as workflow:
        finals = [e for e in workflow.audit.entries("claim_001") if e["event"] == "final_decision"]
    assert len(finals) == 1


def test_a_saved_result_that_no_longer_parses_is_a_recorded_rejection(db):
    _submit(db, "claim_006")
    with _open(db) as workflow:
        config = {"configurable": {"thread_id": "claim_006"}}
        result = workflow.app.get_state(config).values["result"]
        result["risk"]["level"] = "not-a-level"
        workflow.app.update_state(config, {"result": result})
    status = _decide(
        db, "claim_006", adjuster_id="adj-201", next_step=NextStep.PAY, payout_usd=100.0
    )
    assert status["state"] == "waiting_for_adjuster"
    assert "triage result" in status["previous_decision_rejected"]
    with _open(db) as workflow:
        events = [e["event"] for e in workflow.audit.entries("claim_006")]
    assert events[-1] == "decision_rejected"
