"""The adjuster approval gate and its saved checkpoints (fake Claude client, no network)."""

import pytest

from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.governance.approval import AdjusterDecision, build_evidence_pack, evidence_sha256
from hall_monitor.models import NextStep
from hall_monitor.pipeline import triage
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import open_workflow
from tests.fakes import fake_client, outputs_from_truth

CLAIMS = {c.claim_id: c for c in iter_claims()}
REGISTRY = Registry.from_dir()
GATEWAY = ToolGateway(REGISTRY)
WORDING = load_policy_wording()


def _open(path, claim_id=None):
    client = fake_client(outputs_from_truth(load_ground_truth(claim_id))) if claim_id else None
    return open_workflow(path, registry=REGISTRY, gateway=GATEWAY, wording=WORDING, client=client)


def _submit(path, claim_id):
    with _open(path, claim_id) as workflow:
        return workflow.submit(CLAIMS[claim_id])


def _decide(path, claim_id, **fields):
    with _open(path) as workflow:  # a fresh process: nothing but the saved checkpoint
        status = workflow.status(claim_id)
        fields.setdefault("evidence_sha256", status["evidence_sha256"])
        return workflow.decide(claim_id, AdjusterDecision(**fields))


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
        ({"adjuster_id": "adj-999"}, "not a registered adjuster"),
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
