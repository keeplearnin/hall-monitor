"""Milestone 8 offline: redaction, data minimisation, record scope, injection detection,
the adversarial runner and the fairness name swap (fake Claude client, no network)."""

import json
from types import SimpleNamespace

import pytest

from hall_monitor.agents.base import ToolAccess, run_agent
from hall_monitor.agents.recommendation import RecommendationInput
from hall_monitor.data import (
    DATA_DIR,
    iter_claims,
    load_ground_truth,
    load_policy_records,
    load_policy_wording,
)
from hall_monitor.evals.adversarial import build_claim, load_cases, score
from hall_monitor.evals.fairness import NAMES, _forms, name_variant, run, summarize
from hall_monitor.evals.scoring import amount_in, source_text
from hall_monitor.gateway.injection import detect_instructions
from hall_monitor.gateway.redaction import redact_claim, redact_text
from hall_monitor.models import CoverageFinding, Route
from hall_monitor.pipeline import triage
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway
from tests.fakes import fake_client, outputs_from_truth

CLAIMS = {c.claim_id: c for c in iter_claims()}
REGISTRY = Registry.from_dir()
GATEWAY = ToolGateway(REGISTRY)
WORDING = load_policy_wording()
CASES = load_cases()


def _triage(claim, truth_id=None):
    client = fake_client(outputs_from_truth(load_ground_truth(truth_id or claim.claim_id)))
    result = triage(claim, registry=REGISTRY, gateway=GATEWAY, wording=WORDING, client=client)
    return result, client.messages


# --- Redaction ------------------------------------------------------------------------


def test_identifiers_are_redacted_and_evidence_is_kept():
    text = (
        "Call +44 20 7946 0958 or 617-555-0143, email jo@example.com, card 4111 1111 1111 1111, "
        "SSN 123-45-6789, Passport no. X1234567, DOB: 3 March 1988. Paid with card ending 5521. "
        "Flight 2026-08-14, total USD 1,150.00, receipt no. 4471-2290, policy WTP-ESS-100231."
    )
    out = redact_text(text)
    for secret in (
        "7946",
        "555-0143",
        "jo@example.com",
        "4111",
        "123-45-6789",
        "X1234567",
        "1988",
        "5521",
    ):
        assert secret not in out
    for evidence in ("2026-08-14", "1,150.00", "4471-2290", "WTP-ESS-100231"):
        assert evidence in out


@pytest.mark.parametrize("claim_id", sorted(CLAIMS))
def test_redaction_never_removes_evidence_from_a_real_claim(claim_id):
    claim = CLAIMS[claim_id]
    redacted, _ = redact_claim(claim)
    truth = load_ground_truth(claim_id).extraction
    text = source_text(redacted)
    assert all(amount_in(i.amount, text) for i in truth.claimed_items)
    assert truth.policy_number in text


def test_no_identifier_reaches_any_model():
    case = next(c for c in CASES if c["id"] == "adv_10_pii_in_claim")
    claim = build_claim(case, CLAIMS)
    result, messages = _triage(claim, case["base_claim"])
    sent = json.dumps([call["messages"] for call in messages.calls], default=str)
    assert not any(secret in sent for secret in case["secrets"])
    assert result.redactions == {"card_number": 1, "email": 1, "phone": 1}


# --- Data minimisation -------------------------------------------------------------------


def test_recommender_sees_structured_fields_only():
    result, messages = _triage(CLAIMS["claim_004"])  # Crohn's disease in the documents
    [rec_call] = [c for c in messages.calls if c["output_format"].__name__ == "Recommendation"]
    content = rec_call["messages"][0]["content"]
    assert "<claim_summary>" in content
    assert "Aisha Bello" not in content and "Crohn" not in content and "reasoning" not in content
    assert set(RecommendationInput.model_fields) >= {"coverage_decision", "risk_flags"}
    assert REGISTRY.get("recommendation").data_classes == ("financial",)


# --- Record scope ----------------------------------------------------------------------


def test_a_lookup_outside_the_claims_policy_is_refused():
    final = CoverageFinding(applicable_clauses=["2.1"], covered_amount=1.0, reasoning="ok")
    calls = iter(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="t1",
                        name="claims_history",
                        input={"policy_number": "WTP-PLS-200118"},
                    )
                ],
                parsed_output=None,
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            ),
            SimpleNamespace(
                stop_reason="end_turn",
                content=[],
                parsed_output=final,
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            ),
        ]
    )
    access = ToolAccess(GATEWAY, scope_policy="WTP-ESS-101738")
    run_agent(
        "risk_fraud",
        registry=REGISTRY,
        client=SimpleNamespace(messages=SimpleNamespace(parse=lambda **k: next(calls))),
        system="s",
        content="c",
        output_format=CoverageFinding,
        prompt_version="v",
        claim_id="claim_036",
        tools=access,
    )
    [call] = access.log
    assert not call.allowed and "only look up this claim's policy" in call.error


# --- Injection detection -----------------------------------------------------------------


@pytest.mark.parametrize("claim_id", sorted(CLAIMS))
def test_no_real_claim_is_flagged(claim_id):
    assert detect_instructions(CLAIMS[claim_id]) == []


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_detector_flags_what_it_should(case):
    assert bool(detect_instructions(build_claim(case, CLAIMS))) is case["detector_should_flag"]


def test_flagged_claim_goes_to_a_person_even_if_otherwise_automatic():
    case = next(c for c in CASES if c["id"] == "adv_01_override")  # claim_029 is automatic
    assert load_ground_truth("claim_029").route is Route.AUTO
    result, _ = _triage(build_claim(case, CLAIMS), "claim_029")
    assert result.routing.route is Route.ADJUSTER
    assert any("addresses the AI" in r for r in result.routing.reasons)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_adversarial_runner_scores_a_correct_pipeline_as_holding(case):
    result, _ = _triage(build_claim(case, CLAIMS), case["base_claim"])
    scored = score(case, result, trial=1)
    assert not scored.attack_succeeded, scored.how
    assert scored.detector_as_expected


def test_adversarial_runner_catches_an_inflated_payout():
    case = next(c for c in CASES if c["id"] == "adv_08_forged_receipt_note")
    truth = load_ground_truth("claim_029")
    outputs = outputs_from_truth(truth)
    outputs[CoverageFinding] = outputs[CoverageFinding].model_copy(
        update={"covered_amount": 318.40}
    )
    client = fake_client(outputs)
    result = triage(
        build_claim(case, CLAIMS),
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=client,
        critic=False,
    )
    scored = score(case, result, trial=1)
    assert scored.attack_succeeded and "above" in scored.how[0]


# --- Fairness ------------------------------------------------------------------------------


@pytest.mark.parametrize("group", sorted(NAMES))
def test_name_swap_leaves_no_trace_of_the_original(group):
    policies = load_policy_records()
    history = json.loads((DATA_DIR / "claims_history.json").read_text())
    for claim in CLAIMS.values():
        variant, records, renamed = name_variant(claim, policies, history, group, NAMES[group])
        changed = [n for n, r in records.items() if r.insured_name != policies[n].insured_name]
        assert len(changed) == 1
        old = policies[changed[0]].insured_name
        text = variant.narrative + "".join(a.text for a in variant.attachments)
        first, last, airline = _forms(old)
        assert old not in text and airline not in text and last not in text
        assert all(h["insured_name"] != old for h in renamed)


def test_fairness_runner_finds_no_flips_when_the_outcome_cannot_change():
    rows = run(
        ["claim_001"],
        trials=1,
        workers=1,
        registry=REGISTRY,
        client=fake_client(outputs_from_truth(load_ground_truth("claim_001"))),
    )
    summary = summarize(rows)
    assert summary["variant_runs"] == len(NAMES) and summary["flips"] == 0
