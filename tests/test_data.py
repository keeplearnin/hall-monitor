"""Consistency checks on the synthetic dataset and its ground truth."""

import pytest

from hall_monitor.data import (
    DATA_DIR,
    iter_claims,
    load_ground_truth,
    load_policy_records,
    load_policy_wording,
)
from hall_monitor.governance import ADJUSTER_PAYOUT_THRESHOLD_USD
from hall_monitor.models import NextStep, Route

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
    truth = load_ground_truth(claim.claim_id)
    needs_adjuster = (
        truth.next_step in {NextStep.DENY, NextStep.INVESTIGATE}
        or truth.coverage.covered_amount > ADJUSTER_PAYOUT_THRESHOLD_USD
        or bool(truth.risk.flags)
    )
    assert truth.route == (Route.ADJUSTER if needs_adjuster else Route.AUTO)


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: c.claim_id)
def test_cited_clauses_exist_in_wording(claim):
    wording = load_policy_wording()
    for clause in load_ground_truth(claim.claim_id).coverage.applicable_clauses:
        assert f"\n{clause} " in wording, f"clause {clause} not found in policy wording"
