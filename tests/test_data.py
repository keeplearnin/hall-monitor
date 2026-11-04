"""Consistency checks on the synthetic dataset and its ground truth."""

import pytest

from hall_monitor.amounts import source_text
from hall_monitor.data import (
    DATA_DIR,
    iter_claims,
    load_ground_truth,
    load_policy_records,
    load_policy_wording,
)
from hall_monitor.gateway.redaction import redact_claim
from hall_monitor.governance.routing import route_claim
from hall_monitor.models import CoverageAssessment, Recommendation, RiskAssessment

CLAIMS = list(iter_claims())
POLICIES = load_policy_records()


def test_claim_files_are_named_by_id():
    names = sorted(p.stem for p in (DATA_DIR / "claims").glob("*.json"))
    assert names == [c.claim_id for c in CLAIMS]
    assert len(CLAIMS) >= 6


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: c.claim_id)
def test_ground_truth_matches_claim(claim):
    truth = load_ground_truth(claim.claim_id)
    extraction = truth.extraction

    assert truth.claim_id == claim.claim_id
    assert extraction.supporting_documents == [a.filename for a in claim.attachments]
    assert extraction.total_claimed == pytest.approx(
        sum(i.amount for i in extraction.claimed_items)
    )
    assert extraction.policy_number in POLICIES
    assert POLICIES[extraction.policy_number].insured_name == extraction.claimant_name
    assert truth.coverage.covered_amount <= extraction.total_claimed


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: c.claim_id)
def test_route_follows_routing_rule(claim):
    # The answer key's route must be what the real routing rule gives for the right answers.
    truth = load_ground_truth(claim.claim_id)
    coverage = CoverageAssessment(**truth.coverage.model_dump())
    risk = RiskAssessment(**truth.risk.model_dump(), reasoning="")
    recommendation = Recommendation(next_step=truth.next_step, reasons=[])
    policy = POLICIES.get(truth.extraction.policy_number)
    routing = route_claim(
        truth.extraction,
        coverage,
        risk,
        recommendation,
        policy=policy,
        claim_text=source_text(redact_claim(claim)[0]),
    )
    assert routing.route == truth.route, routing.reasons


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: c.claim_id)
def test_cited_clauses_exist_in_wording(claim):
    wording = load_policy_wording()
    for clause in load_ground_truth(claim.claim_id).coverage.applicable_clauses:
        assert f"\n{clause} " in wording, f"clause {clause} not found in policy wording"
