"""Pipeline wiring and the routing rule, with the Claude client faked out (no network)."""

import pytest

from hall_monitor.data import (
    iter_claims,
    load_ground_truth,
    load_policy_records,
    load_policy_wording,
)
from hall_monitor.governance.routing import route_claim
from hall_monitor.models import (
    CoverageAssessment,
    CoverageDecision,
    CoverageFinding,
    CriticIssue,
    CriticReview,
    IntakeExtraction,
    NextStep,
    Recommendation,
    ReviewedStage,
    RiskAssessment,
    RiskFlag,
    RiskLevel,
    Route,
)
from hall_monitor.pipeline import triage
from hall_monitor.registry import Registry
from tests.fakes import fake_client, outputs_from_truth

CLAIMS = list(iter_claims())
REGISTRY = Registry.from_dir()


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: c.claim_id)
def test_correct_agent_outputs_give_the_expected_route(claim):
    truth = load_ground_truth(claim.claim_id)
    client = fake_client(outputs_from_truth(truth))

    result = triage(
        claim,
        registry=REGISTRY,
        policies=load_policy_records(),
        wording=load_policy_wording(),
        client=client,
    )

    assert result.routing.route == truth.route
    assert [c.agent_id for c in result.agent_calls] == [
        "intake",
        "coverage_check",
        "risk_fraud",
        "critic",
        "recommendation",
    ]
    assert result.coverage.decision == truth.coverage.decision
    for call, sent in zip(result.agent_calls, client.messages.calls, strict=True):
        assert sent["model"] == REGISTRY.get(call.agent_id).model == call.model


def test_coverage_check_sees_the_policy_record_and_wording():
    claim = CLAIMS[0]
    client = fake_client(outputs_from_truth(load_ground_truth(claim.claim_id)))
    triage(
        claim,
        registry=REGISTRY,
        policies=load_policy_records(),
        wording=load_policy_wording(),
        client=client,
    )
    coverage_call = client.messages.calls[1]
    assert "<policy_wording>" in coverage_call["system"]
    assert "WTP-ESS-100231" in coverage_call["messages"][0]["content"]


def test_pipeline_stops_if_an_agent_loses_its_hall_pass():
    registry = Registry(m for m in REGISTRY if m.id != "risk_fraud")
    claim = CLAIMS[0]
    client = fake_client(outputs_from_truth(load_ground_truth(claim.claim_id)))
    with pytest.raises(Exception, match="risk_fraud"):
        triage(
            claim,
            registry=registry,
            policies=load_policy_records(),
            wording=load_policy_wording(),
            client=client,
        )
    assert len(client.messages.calls) == 2  # intake and coverage ran; nothing after


def _route(amount=100.0, flags=(), step=NextStep.PAY):
    coverage = CoverageAssessment(
        decision=CoverageDecision.COVERED,
        applicable_clauses=[],
        covered_amount=amount,
        reasoning="",
    )
    risk = RiskAssessment(level=RiskLevel.LOW, flags=list(flags), reasoning="")
    return route_claim(coverage, risk, Recommendation(next_step=step, reasons=[]))


def test_small_clean_payment_is_automatic():
    assert _route().route is Route.AUTO


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": 2_500.01},
        {"flags": [RiskFlag.DUPLICATE_RECEIPT]},
        {"step": NextStep.DENY},
        {"step": NextStep.INVESTIGATE},
    ],
)
def test_consequential_claims_go_to_an_adjuster(kwargs):
    decision = _route(**kwargs)
    assert decision.route is Route.ADJUSTER
    assert decision.reasons


# --- Policy lookup gate -------------------------------------------------------

POLICIES = load_policy_records()
WORDING = load_policy_wording()


def _triage(claim, outputs, **kwargs):
    client = fake_client(outputs)
    result = triage(
        claim, registry=REGISTRY, policies=POLICIES, wording=WORDING, client=client, **kwargs
    )
    return result, client.messages


def test_missing_policy_number_gets_one_retry():
    claim = CLAIMS[0]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    good = outputs[IntakeExtraction]
    outputs[IntakeExtraction] = [good.model_copy(update={"policy_number": None}), good]

    result, messages = _triage(claim, outputs)

    assert messages.formats()[:2] == ["IntakeExtraction", "IntakeExtraction"]
    assert "No policy record matches" in messages.calls[1]["messages"][0]["content"]
    assert result.stopped_reason is None
    assert result.extraction.policy_number == good.policy_number


def test_pipeline_stops_when_the_policy_cant_be_found():
    claim = CLAIMS[0]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    outputs[IntakeExtraction] = outputs[IntakeExtraction].model_copy(
        update={"policy_number": "WTP-NOPE"}
    )

    result, messages = _triage(claim, outputs)

    assert messages.formats() == ["IntakeExtraction", "IntakeExtraction"]
    assert result.stopped_reason
    assert result.coverage is None and result.risk is None
    assert result.recommendation.next_step is NextStep.REQUEST_INFORMATION
    assert result.routing.route is Route.ADJUSTER


# --- Coverage label ------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "decision"),
    [
        (640.0, CoverageDecision.COVERED),
        (540.0, CoverageDecision.PARTIALLY_COVERED),
        (0.0, CoverageDecision.NOT_COVERED),
    ],
)
def test_coverage_label_follows_the_amount(amount, decision):
    finding = CoverageFinding(applicable_clauses=["4.1"], covered_amount=amount, reasoning="")
    assert CoverageAssessment.from_finding(finding, total_claimed=640.0).decision is decision


# --- Critic loop -----------------------------------------------------------------


def _issue(stage, problem="Amount ignores the 3,000 limit."):
    return CriticReview(issues=[CriticIssue(stage=stage, problem=problem, evidence="2.2")])


def test_critic_sends_coverage_back_and_approves_the_revision():
    claim = next(c for c in CLAIMS if c.claim_id == "claim_008")
    truth = load_ground_truth(claim.claim_id)
    outputs = outputs_from_truth(truth)
    wrong = outputs[CoverageFinding].model_copy(update={"covered_amount": 3640.0})
    outputs[CoverageFinding] = [wrong, outputs[CoverageFinding]]
    outputs[CriticReview] = [_issue(ReviewedStage.COVERAGE_CHECK), CriticReview(issues=[])]

    result, messages = _triage(claim, outputs)

    assert messages.formats() == [
        "IntakeExtraction",
        "CoverageFinding",
        "RiskAssessment",
        "CriticReview",
        "CoverageFinding",
        "CriticReview",
        "Recommendation",
    ]
    revised_request = messages.calls[4]["messages"][0]["content"]
    assert "<reviewer_feedback>" in revised_request and "3,000 limit" in revised_request
    assert result.coverage.covered_amount == 3000.0
    assert result.revisions == 1 and not result.critic_unresolved


def test_intake_correction_reruns_coverage_and_risk():
    claim = CLAIMS[0]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    outputs[CriticReview] = [_issue(ReviewedStage.INTAKE, "Wrong date."), CriticReview(issues=[])]

    _, messages = _triage(claim, outputs)

    assert messages.formats()[4:7] == ["IntakeExtraction", "CoverageFinding", "RiskAssessment"]


def test_unresolved_disagreement_goes_to_an_adjuster():
    claim = next(c for c in CLAIMS if c.claim_id == "claim_001")  # otherwise automatic
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    outputs[CriticReview] = [_issue(ReviewedStage.RISK_FRAUD, "Missing flag.")]

    result, messages = _triage(claim, outputs)

    assert messages.formats().count("CriticReview") == 3  # review, revise, review, revise, review
    assert result.revisions == 2 and result.critic_unresolved
    assert result.routing.route is Route.ADJUSTER
    assert any("critic" in r for r in result.routing.reasons)


def test_critic_can_be_switched_off():
    claim = CLAIMS[0]
    _, messages = _triage(
        claim, outputs_from_truth(load_ground_truth(claim.claim_id)), critic=False
    )
    assert "CriticReview" not in messages.formats()
