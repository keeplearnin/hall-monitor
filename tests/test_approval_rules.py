"""The adjuster decision rules, checked directly (no workflow, no network)."""

import pytest

from hall_monitor.data import load_ground_truth
from hall_monitor.governance.approval import (
    DecisionRejected,
    audit_payload,
    final_auto,
    final_from_adjuster,
    load_adjusters,
    validate_decision,
)
from hall_monitor.governance.routing import route_stopped
from hall_monitor.models import (
    CoverageAssessment,
    NextStep,
    Recommendation,
    RiskAssessment,
    RiskLevel,
    Route,
    RoutingDecision,
    TriageResult,
)
from tests.fakes import signed_decision

ADJUSTERS = load_adjusters()
PACK = "e" * 64
REASON = "Checked the documents myself; payment is supported."


def _triaged(claim_id: str, route: Route = Route.ADJUSTER) -> TriageResult:
    truth = load_ground_truth(claim_id)
    return TriageResult(
        claim_id=claim_id,
        extraction=truth.extraction,
        coverage=CoverageAssessment(**truth.coverage.model_dump()),
        risk=RiskAssessment(**truth.risk.model_dump(), reasoning=""),
        recommendation=Recommendation(next_step=truth.next_step, reasons=[]),
        routing=RoutingDecision(route=route, reasons=[]),
        agent_calls=[],
    )


def _stopped(claim_id: str, *, extracted: bool) -> TriageResult:
    truth = load_ground_truth(claim_id)
    reason = "agent refused or returned no usable output"
    return TriageResult(
        claim_id=claim_id,
        extraction=truth.extraction if extracted else None,
        coverage=None,
        risk=None,
        recommendation=Recommendation(next_step=NextStep.REQUEST_INFORMATION, reasons=[reason]),
        routing=route_stopped(reason),
        agent_calls=[],
        stopped_reason=reason,
    )


def _decide(result: TriageResult, adjuster: str, step: NextStep, payout: float = 0.0, **extra):
    decision = signed_decision(
        result.claim_id,
        adjuster_id=adjuster,
        next_step=step,
        payout_usd=payout,
        reason=extra.get("reason", REASON),
        evidence_sha256=PACK,
    )
    return decision, validate_decision(decision, result, PACK, ADJUSTERS)


@pytest.mark.parametrize("extracted", [True, False], ids=["risk-unassessed", "nothing-extracted"])
def test_paying_a_claim_the_ai_never_assessed_needs_a_senior_adjuster(extracted):
    result = _stopped("claim_001", extracted=extracted)
    with pytest.raises(DecisionRejected, match="senior adjuster"):
        _decide(result, "adj-101", NextStep.PAY, 100.0)
    _decide(result, "adj-201", NextStep.PAY, 100.0)  # the senior adjuster may


def test_denying_more_than_your_authority_needs_a_senior_adjuster():
    result = _triaged("claim_003")  # 6,480 claimed; adj-101's authority is 5,000
    with pytest.raises(DecisionRejected, match="authority"):
        _decide(result, "adj-101", NextStep.DENY)
    _decide(result, "adj-201", NextStep.DENY)


def test_a_demo_key_signature_is_marked_in_the_record():
    result = _triaged("claim_003")
    decision, adjuster = _decide(result, "adj-201", NextStep.PAY, 6480.0)
    final = final_from_adjuster(decision, adjuster, result, PACK)
    assert final.signed_with_demo_key is True
    assert audit_payload(final, "h", decision.signature)["signed_with_demo_key"] is True


@pytest.mark.parametrize(
    ("step", "payout", "overrode"),
    [(NextStep.PAY, 6480.0, False), (NextStep.PAY, 6000.0, True), (NextStep.INVESTIGATE, 0, True)],
)
def test_the_recorded_override_matches_the_rule_that_demanded_a_reason(step, payout, overrode):
    result = _triaged("claim_003")  # the AI recommends paying 6,480
    decision, adjuster = _decide(result, "adj-201", step, payout)
    assert final_from_adjuster(decision, adjuster, result, PACK).overrode_ai is overrode
    if overrode:
        with pytest.raises(DecisionRejected, match="reason"):
            _decide(result, "adj-201", step, payout, reason="")


def test_an_automatic_decision_needs_an_automatic_route():
    with pytest.raises(ValueError, match="routed automatically"):
        final_auto(_triaged("claim_003", route=Route.ADJUSTER), PACK)


# --- Phase A5: from the end-of-phase review ---------------------------------------------


def _in_currency(claim_id: str, currency: str, total: float) -> TriageResult:
    result = _triaged(claim_id)
    extraction = result.extraction.model_copy(update={"currency": currency, "total_claimed": total})
    return result.model_copy(update={"extraction": extraction})


@pytest.mark.parametrize(
    ("step", "payout"), [(NextStep.PAY, 4900.0), (NextStep.DENY, 0.0)], ids=["pay", "deny"]
)
def test_a_claim_not_in_usd_needs_a_senior_adjuster(step, payout):
    result = _in_currency("claim_001", "JPY", 100_000)  # about $650: limits are in USD
    with pytest.raises(DecisionRejected, match="senior adjuster"):
        _decide(result, "adj-101", step, payout)
    _decide(result, "adj-201", step, payout)


def test_denying_a_claim_nothing_was_extracted_from_needs_a_senior_adjuster():
    result = _stopped("claim_001", extracted=False)  # the amount claimed is unknown
    with pytest.raises(DecisionRejected, match="senior adjuster"):
        _decide(result, "adj-101", NextStep.DENY)
    _decide(result, "adj-201", NextStep.DENY)


def test_paying_a_risky_claim_with_no_flags_needs_a_senior_adjuster():
    result = _triaged("claim_001")
    risky = RiskAssessment(level=RiskLevel.HIGH, flags=[], reasoning="")
    result = result.model_copy(update={"risk": risky})
    with pytest.raises(DecisionRejected, match="senior adjuster"):
        _decide(result, "adj-101", NextStep.PAY, 100.0)
