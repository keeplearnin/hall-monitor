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
    NextStep,
    Recommendation,
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
        "recommendation",
    ]
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
