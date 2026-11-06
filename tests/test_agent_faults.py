"""Agents that get it wrong: each fault is injected into an otherwise correct run, on every claim
the answer key routes automatically, and must still reach a person.

The fake client (tests/fakes.py) answers with the answer key, so on its own it only shows the
happy path. Here each answer is bent the way a model could plausibly bend it: a wrong name, a
date outside cover, a payout above the claim, a fraud flag with a "low" label, a truncated or
missing answer, an API failure. Faults that code can't see (a wrong answer consistent with every
fact code can check) are pinned at the end as known limits, so adding a control flips them.
"""

import re
from datetime import timedelta

import anthropic
import httpx
import pytest

from hall_monitor.data import (
    iter_claims,
    load_ground_truth,
    load_policy_records,
    load_policy_wording,
)
from hall_monitor.models import (
    ClaimedItem,
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
from hall_monitor.tools.gateway import ToolGateway
from tests.fakes import TEST_REGISTRY_DIR, fake_client, outputs_from_truth

REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
GATEWAY = ToolGateway(REGISTRY)
WORDING = load_policy_wording()
POLICIES = load_policy_records()
AUTO_CLAIMS = [c for c in iter_claims() if load_ground_truth(c.claim_id).route is Route.AUTO]
AGENT_OUTPUTS = (IntakeExtraction, CoverageFinding, RiskAssessment, CriticReview, Recommendation)


def _update(outputs: dict, kind: type, **fields) -> None:
    outputs[kind] = outputs[kind].model_copy(update=fields)


def _intake(outputs: dict) -> IntakeExtraction:
    return outputs[IntakeExtraction]


def _policy(outputs: dict):
    return POLICIES[_intake(outputs).policy_number]


def _other_policy(outputs: dict) -> str:
    mine = _policy(outputs)
    return next(n for n, p in POLICIES.items() if p.insured_name != mine.insured_name)


def _pay(outputs: dict, amount: float) -> None:
    _update(outputs, CoverageFinding, covered_amount=amount)


def _add_items(outputs: dict, *amounts: float) -> None:
    """Intake lists extra items; the total and the payout follow, so only the trace can tell."""
    intake = _intake(outputs)
    extra = [ClaimedItem(description="extra", amount=a) for a in amounts]
    items = [*intake.claimed_items, *extra]
    total = round(sum(i.amount for i in items), 2)
    _update(outputs, IntakeExtraction, claimed_items=items, total_claimed=total)
    _pay(outputs, total)


def _only_item(outputs: dict, amount: float) -> None:
    """Intake takes one figure as the whole claim; the total and the payout follow."""
    item = ClaimedItem(description="claimed", amount=amount)
    _update(outputs, IntakeExtraction, claimed_items=[item], total_claimed=amount)
    _pay(outputs, amount)


def _inflate(outputs: dict, factor: float) -> None:
    """Intake reports larger amounts than the documents show; coverage pays them in full."""
    intake = _intake(outputs)
    items = [
        ClaimedItem(description=i.description, amount=round(i.amount * factor, 2))
        for i in intake.claimed_items
    ]
    total = round(sum(i.amount for i in items), 2)
    _update(outputs, IntakeExtraction, claimed_items=items, total_claimed=total)
    _pay(outputs, total)


# Each fault: (how it bends the answers, a phrase the routing reasons must contain).
FAULTS = {
    "intake: claimant isn't the insured": (
        lambda o: _update(o, IntakeExtraction, claimant_name="Morgan Example"),
        "claimant is not the insured",
    ),
    "intake: another insured's policy": (
        lambda o: _update(o, IntakeExtraction, policy_number=_other_policy(o)),
        "claimant is not the insured",
    ),
    "intake: incident before cover starts": (
        lambda o: _update(
            o, IntakeExtraction, incident_date=_policy(o).coverage_start - timedelta(days=1)
        ),
        "outside the coverage period",
    ),
    "intake: incident after cover ends": (
        lambda o: _update(
            o, IntakeExtraction, incident_date=_policy(o).coverage_end + timedelta(days=1)
        ),
        "outside the coverage period",
    ),
    "intake: no incident date": (
        lambda o: _update(o, IntakeExtraction, incident_date=None),
        "no incident date",
    ),
    "intake: another currency": (
        lambda o: _update(o, IntakeExtraction, currency="EUR"),
        "not USD",
    ),
    "intake: total below the payout": (
        lambda o: _update(
            o, IntakeExtraction, total_claimed=o[CoverageFinding].covered_amount - 50
        ),
        "exceeds the",
    ),
    "intake and coverage: amounts not in the documents": (
        lambda o: _inflate(o, 1.1),
        "not in the claim documents",
    ),
    "intake: an item counted twice": (
        lambda o: _add_items(o, _intake(o).claimed_items[0].amount),
        "not in the claim documents",
    ),
    "intake: the year of a date taken as the whole claim": (
        lambda o: _only_item(o, float(_intake(o).incident_date.year)),
        "not in the claim documents",
    ),
    "intake: the total line counted as an item": (
        lambda o: _add_items(o, _intake(o).total_claimed),
        "not in the claim documents",
    ),
    "intake: a date counted as an amount": (
        lambda o: _add_items(o, 20),
        "not in the claim documents",
    ),
    "intake: total isn't the sum of the items": (
        lambda o: (
            _update(o, IntakeExtraction, total_claimed=_intake(o).total_claimed + 100),
            _pay(o, _intake(o).total_claimed),
        ),
        "don't add up",
    ),
    "coverage: pays more than claimed": (
        lambda o: _pay(o, _intake(o).total_claimed + 0.01),
        "exceeds the",
    ),
    "coverage: pays above the threshold": (
        lambda o: _pay(o, 2_500.01),
        "above 2,500 threshold",
    ),
    "coverage: pays nothing": (lambda o: _pay(o, 0), "nothing covered"),
    "coverage: negative payout": (lambda o: _pay(o, -10), "nothing covered"),
    "risk: flag with a low label": (
        lambda o: _update(o, RiskAssessment, flags=[RiskFlag.DUPLICATE_RECEIPT]),
        "fraud flags",
    ),
    "risk: medium with no flags": (
        lambda o: _update(o, RiskAssessment, level=RiskLevel.MEDIUM),
        "medium risk",
    ),
    "risk: high with no flags": (
        lambda o: _update(o, RiskAssessment, level=RiskLevel.HIGH),
        "high risk",
    ),
    "recommendation: deny a covered claim": (
        lambda o: _update(o, Recommendation, next_step=NextStep.DENY),
        "recommended deny",
    ),
    "recommendation: investigate": (
        lambda o: _update(o, Recommendation, next_step=NextStep.INVESTIGATE),
        "recommended investigate",
    ),
    "critic: never satisfied": (
        lambda o: o.update(
            {
                CriticReview: CriticReview(
                    issues=[
                        CriticIssue(
                            stage=ReviewedStage.RISK_FRAUD, problem="Unsure.", evidence="n/a"
                        )
                    ]
                )
            }
        ),
        "critic still disagrees",
    ),
}


def _run(claim, outputs, **kwargs):
    client = fake_client(outputs, **kwargs)
    return triage(claim, registry=REGISTRY, gateway=GATEWAY, wording=WORDING, client=client)


def test_the_unbent_answers_route_automatically():
    """The baseline the faults are measured against: without a fault, these claims are AUTO."""
    assert AUTO_CLAIMS
    for claim in AUTO_CLAIMS:
        result = _run(claim, outputs_from_truth(load_ground_truth(claim.claim_id)))
        assert result.routing.route is Route.AUTO, (claim.claim_id, result.routing.reasons)


@pytest.mark.parametrize("fault", list(FAULTS))
@pytest.mark.parametrize("claim", AUTO_CLAIMS, ids=lambda c: c.claim_id)
def test_a_wrong_answer_sends_the_claim_to_a_person(claim, fault):
    bend, expected_reason = FAULTS[fault]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    bend(outputs)

    result = _run(claim, outputs)

    assert result.routing.route is Route.ADJUSTER
    assert any(expected_reason in r for r in result.routing.reasons), result.routing.reasons


def _rewritten(claim, rewrite):
    attachments = [a.model_copy(update={"text": rewrite(a.text)}) for a in claim.attachments]
    narrative = rewrite(claim.narrative)
    return claim.model_copy(update={"narrative": narrative, "attachments": attachments})


# The same claim in another currency, each way a document might write it.
CURRENCIES = {
    "EUR 1.00": lambda t: t.replace("USD", "EUR").replace("$", "EUR "),
    "CAD $1.00": lambda t: (
        t.replace("USD", "CAD $").replace("$", "CAD $").replace("CAD CAD $", "CAD $")
    ),
    "$1.00 CAD": lambda t: re.sub(r"(?:USD |\$)([\d,]+\.\d\d)", r"$\1 CAD", t),
    "HK $1.00": lambda t: re.sub(r"(?:USD |\$)([\d,]+\.\d\d)", r"HK $\1", t),
    "$1.00 (cad)": lambda t: re.sub(r"(?:USD |\$)([\d,]+\.\d\d)", r"$\1 (cad)", t),
}


@pytest.mark.parametrize("currency", list(CURRENCIES))
@pytest.mark.parametrize("claim", AUTO_CLAIMS, ids=lambda c: c.claim_id)
def test_a_claim_in_another_currency_labelled_usd_goes_to_a_person(claim, currency):
    """The currency label is the model's; the trace only counts figures the documents show in
    US dollars, so a claim in another currency the intake calls USD isn't paid 1:1."""
    claim = _rewritten(claim, CURRENCIES[currency])
    result = _run(claim, outputs_from_truth(load_ground_truth(claim.claim_id)))

    assert result.routing.route is Route.ADJUSTER
    assert any("not in the claim documents" in r for r in result.routing.reasons)


def _missing(kwargs):
    return None


def _wrong_type(kwargs):
    return Recommendation(next_step=NextStep.PAY, reasons=["not what was asked for"])


def _api_down(kwargs):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    raise anthropic.APIConnectionError(request=request)


def _bug(kwargs):
    raise KeyError("a bug in the client")


BROKEN = {"no output": _missing, "API down": _api_down, "unexpected error": _bug}


@pytest.mark.parametrize("broken", list(BROKEN))
@pytest.mark.parametrize("kind", AGENT_OUTPUTS, ids=lambda k: k.__name__)
def test_a_missing_or_failed_answer_stops_the_claim_for_a_person(kind, broken):
    claim = AUTO_CLAIMS[0]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    outputs[kind] = BROKEN[broken]

    result = _run(claim, outputs)

    assert result.routing.route is Route.ADJUSTER
    assert result.stopped_reason
    assert result.coverage is None and result.risk is None


@pytest.mark.parametrize(
    "kind", [k for k in AGENT_OUTPUTS if k is not Recommendation], ids=lambda k: k.__name__
)
def test_an_answer_of_the_wrong_type_stops_the_claim_for_a_person(kind):
    claim = AUTO_CLAIMS[0]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    outputs[kind] = _wrong_type

    result = _run(claim, outputs)

    assert result.routing.route is Route.ADJUSTER
    assert result.stopped_reason


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal", "pause_turn"])
@pytest.mark.parametrize("kind", AGENT_OUTPUTS, ids=lambda k: k.__name__)
def test_an_incomplete_answer_stops_the_claim_for_a_person(kind, stop_reason):
    claim = AUTO_CLAIMS[0]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))

    result = _run(claim, outputs, stop_reason={kind: stop_reason})

    assert result.routing.route is Route.ADJUSTER
    assert result.stopped_reason


# Known limits: wrong answers that agree with every fact code can check. Code can't catch them;
# the critic, the evals and the adjuster's sampling are what stand against them. These tests
# pin the limit so a new control that catches one fails here and gets the docs updated.


def test_known_limit_a_missed_fraud_signal_routes_automatically():
    claim = AUTO_CLAIMS[0]
    outputs = outputs_from_truth(load_ground_truth(claim.claim_id))
    _update(outputs, RiskAssessment, level=RiskLevel.LOW, flags=[], reasoning="Looks fine.")

    assert _run(claim, outputs).routing.route is Route.AUTO


def test_known_limit_an_overpayment_within_the_claim_routes_automatically():
    """Coverage ignores a limit and pays the whole claim, which is still within bounds."""
    truth = load_ground_truth("claim_010")  # the policy pays 1,070 of the 1,520 claimed
    assert truth.coverage.covered_amount < truth.extraction.total_claimed
    claim = next(c for c in AUTO_CLAIMS if c.claim_id == "claim_010")
    outputs = outputs_from_truth(truth)
    _pay(outputs, truth.extraction.total_claimed)

    assert _run(claim, outputs).routing.route is Route.AUTO
