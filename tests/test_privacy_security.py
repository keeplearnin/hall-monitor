"""Milestone 8 offline: redaction, data minimisation, record scope, injection detection,
the adversarial runner and the fairness name swap (fake Claude client, no network)."""

import json
from types import SimpleNamespace

import pytest

from hall_monitor.agents import recommendation, risk_fraud
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
from hall_monitor.evals.fairness import NAMES, _forms, name_variant, run, summarize, swap_name
from hall_monitor.evals.scoring import amount_in, source_text
from hall_monitor.gateway.injection import detect_instructions
from hall_monitor.gateway.redaction import redact_claim, redact_text
from hall_monitor.models import CoverageFinding, IntakeExtraction, Route
from hall_monitor.pipeline import triage
from hall_monitor.registry import DataClassNotAllowedError, Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.tools.server import build_server
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


@pytest.mark.parametrize(
    "text, secret, kind",
    [
        ("Clinic tax ID: 12-3456789 on the invoice.", "3456789", "tin"),
        ("EIN 98-7654321", "7654321", "tin"),
        ("Provider TIN 123456789.", "123456789", "tin"),
        ("Refund to account number 000123456789.", "123456789", "bank_account"),
        ("Acct # 4455 6677 8899", "6677", "bank_account"),
        ("bank account ending in 0042", "0042", "bank_account"),
        ("Routing number: 021000021", "021000021", "bank_routing"),
        ("ABA 011000015", "011000015", "bank_routing"),
        ("Pay IBAN GB82 WEST 1234 5698 7654 32, thanks.", "5698", "iban"),
        ("IBAN DE89370400440532013000", "0532013000", "iban"),
    ],
)
def test_tax_ids_and_bank_details_are_redacted(text, secret, kind):
    from collections import Counter

    counts = Counter()
    out = redact_text(text, counts)
    assert secret not in out
    assert counts[kind] == 1


@pytest.mark.parametrize(
    "text",
    [
        "Receipt no. 4471-2290, total USD 1,150.00 on 2026-08-14.",
        "Booking ref 12-34, flight BA0117, policy WTP-ESS-100231.",
        "Account of events: the bag was lost on 14 August 2026.",
        "GB82 is not an IBAN without its digits.",
        "IBAN GB00 WEST 1234 5698 7654 32 fails its checksum.",
    ],
)
def test_bank_and_tax_patterns_leave_ordinary_evidence_alone(text):
    assert redact_text(text) == text


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
    # Its claim type and clause numbers can reveal a medical claim, so it is declared (and
    # approved) for health data, though it sees no medical text.
    assert set(REGISTRY.get("recommendation").data_classes) == {"financial", "health"}


def test_an_input_beyond_the_manifests_data_classes_is_refused_before_the_model():
    def parse(**kwargs):
        raise AssertionError("the model must not be called")

    with pytest.raises(DataClassNotAllowedError, match="may not see pii data"):
        run_agent(
            "recommendation",
            registry=REGISTRY,
            client=SimpleNamespace(messages=SimpleNamespace(parse=parse)),
            system=recommendation.SYSTEM_PROMPT,
            content="the whole claim",  # carries every class unless the caller says otherwise
            output_format=CoverageFinding,
            prompt_version="v",
            claim_id="claim_004",
        )


def test_a_tool_beyond_the_agents_data_classes_is_refused():
    no_health = REGISTRY.get("risk_fraud").model_copy(update={"data_classes": ("pii", "financial")})
    registry = Registry([no_health])  # unapproved copy: approval checks off for this test
    log = []
    gateway = ToolGateway(registry)
    result = gateway.call("risk_fraud", "claims_history", {"policy_number": "WTP-ESS-100231"}, log)
    assert "may not see health" in result["error"]
    assert not log[0].allowed


def test_identifiers_are_redacted_from_tool_results_and_file_names():
    history = [
        {
            "insured_name": "Daniel Okafor",
            "reference": "CLM-1",
            "summary": "Called from +1 415 555 0134; email d.okafor@example.com.",
        }
    ]
    gateway = ToolGateway(REGISTRY, server=build_server(history=history))
    result = gateway.call("risk_fraud", "claims_history", {"policy_number": "WTP-ESS-100231"}, [])
    summary = result["claims"][0]["summary"]
    assert "example.com" not in summary and "555" not in summary

    claim = CLAIMS["claim_001"]
    attachment = claim.attachments[0].model_copy(update={"filename": "jo.smith@example.com.pdf"})
    redacted, counts = redact_claim(claim.model_copy(update={"attachments": [attachment]}))
    assert "example.com" not in redacted.attachments[0].filename and counts["email"] >= 1


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
        prompt_template=risk_fraud.SYSTEM_PROMPT,
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
    truth = load_ground_truth("claim_001")
    outputs = outputs_from_truth(truth)

    def intake(request):
        # Read the claimant's name from the claim, as the intake agent would.
        content = request["messages"][0]["content"]
        name = next(n for n in [*NAMES.values(), truth.extraction.claimant_name] if n in content)
        return truth.extraction.model_copy(update={"claimant_name": name})

    outputs[IntakeExtraction] = intake
    rows = run(["claim_001"], trials=1, workers=1, registry=REGISTRY, client=fake_client(outputs))
    summary = summarize(rows)
    assert summary["variant_runs"] == len(NAMES) and summary["flips"] == 0


def test_name_variant_uses_the_claims_own_policy_not_the_first_name_mentioned():
    policies = load_policy_records()
    history = json.loads((DATA_DIR / "claims_history.json").read_text())
    claim = CLAIMS["claim_002"]  # Mei Lin Zhou's claim, mentioning Daniel Okafor (listed first)
    mention = claim.model_copy(
        update={"narrative": claim.narrative + " Daniel Okafor travelled with me."}
    )
    _, records, _ = name_variant(mention, policies, history, "anglo", NAMES["anglo"])
    changed = [n for n, r in records.items() if r.insured_name != policies[n].insured_name]
    assert changed == [load_ground_truth("claim_002").extraction.policy_number]


def test_swapping_a_first_name_leaves_dates_alone():
    text = "June Park flew on June 14. June said the bag never arrived."
    assert swap_name(text, "June Park", "Wei Chen") == (
        "Wei Chen flew on June 14. Wei said the bag never arrived."
    )
