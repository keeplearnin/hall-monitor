"""The eval's scoring rules: ground truth scores perfectly, and known errors are caught."""

import re

import pytest

from hall_monitor.claims.amounts import source_text
from hall_monitor.claims.data import (
    iter_claims,
    load_ground_truth,
    load_policy_records,
    load_policy_wording,
)
from hall_monitor.claims.evals.scoring import (
    amount_in,
    clause_exists,
    failed_run,
    is_infrastructure_stop,
    location_matches,
    normalize_clause,
    score_run,
    summarize,
    untraced,
)
from hall_monitor.claims.models import (
    ClaimedItem,
    CoverageAssessment,
    Recommendation,
    RiskAssessment,
    TriageResult,
)
from hall_monitor.claims.routing import route_claim
from hall_monitor.claims.screening import redact_claim
from hall_monitor.gateway.cost import call_cost
from hall_monitor.shared_models import AgentCall

CLAIMS = {c.claim_id: c for c in iter_claims()}
WORDING = load_policy_wording()


def result_from_truth(claim_id: str) -> TriageResult:
    truth = load_ground_truth(claim_id)
    coverage = CoverageAssessment(**truth.coverage.model_dump())
    risk = RiskAssessment(**truth.risk.model_dump(), reasoning="")
    recommendation = Recommendation(next_step=truth.next_step, reasons=[])
    return TriageResult(
        claim_id=claim_id,
        extraction=truth.extraction,
        coverage=coverage,
        risk=risk,
        recommendation=recommendation,
        routing=route_claim(
            truth.extraction,
            coverage,
            risk,
            recommendation,
            policy=load_policy_records().get(truth.extraction.policy_number),
            claim_text=source_text(redact_claim(CLAIMS[claim_id])[0]),
        ),
        agent_calls=[],
    )


def score(result: TriageResult):
    claim_id = result.claim_id
    return score_run(
        result, load_ground_truth(claim_id), CLAIMS[claim_id], WORDING, trial=1, cost_usd=0.0
    )


@pytest.mark.parametrize("claim_id", sorted(CLAIMS))
def test_ground_truth_scores_perfectly(claim_id):
    s = score(result_from_truth(claim_id))
    assert all(s.extraction_fields.values()), s.extraction_fields
    assert s.coverage_correct and s.risk_level and s.next_step and s.route
    assert s.unsupported == []


def test_invented_amount_and_clause_count_as_hallucinations():
    result = result_from_truth("claim_001")
    result.extraction.claimed_items.append(ClaimedItem(description="Taxi", amount=57.25))
    result.coverage.applicable_clauses.append("9.9")
    s = score(result)
    assert s.unsupported == ["amount=57.25", "clause=9.9"]
    assert not s.extraction_fields["claimed_items"]


def test_an_item_counted_more_often_than_it_appears_is_a_hallucination():
    """Scoring traces amounts as the routing rule does: each item to its own occurrence. The
    842.00 flight is in the narrative and on the airline's letter, so a third is unsupported."""
    result = result_from_truth("claim_001")
    flight = result.extraction.claimed_items[0]
    assert flight.amount == 842.0
    result.extraction.claimed_items += [flight.model_copy(), flight.model_copy()]
    s = score(result)
    assert s.unsupported == ["amount=842.00"]


def test_missing_deciding_clause_fails_coverage():
    result = result_from_truth("claim_005")
    result.coverage.applicable_clauses = ["3.2"]
    s = score(result)
    assert s.clause_recall == 0.5
    assert s.coverage_decision and not s.coverage_correct


@pytest.mark.parametrize(
    ("predicted", "expected", "match"),
    [
        (None, None, True),
        ("Barcelona", None, False),
        (None, "Dublin, Ireland", False),
        ("Haneda Airport, Tokyo", "Tokyo (Haneda), Japan", True),
        ("St Bartholomew's Hospital, London", "St. Bartholomew's Hospital", True),
        ("Madrid airport", "Barcelona, Spain", False),
    ],
)
def test_location_matching(predicted, expected, match):
    assert location_matches(predicted, expected) is match


def test_clause_helpers():
    assert normalize_clause("2.1(a)") == "2.1"
    assert normalize_clause("Section 5.1") == "5.1"
    assert clause_exists("3.3", WORDING)
    assert not clause_exists("3.4", WORDING)


def test_failed_runs_count_against_every_metric():
    good = score(result_from_truth("claim_001"))
    summary = summarize([good, failed_run("claim_001", 2, "APIError: boom")])
    assert summary["errors"] == 1
    assert summary["extraction_accuracy"] == 0.5
    assert summary["coverage_reasoning_accuracy"] == 0.5


def test_call_cost_uses_list_prices():
    call = AgentCall(
        agent_id="coverage_check",
        agent_version="0.1.0",
        model="claude-sonnet-5-5",
        manifest_digest="x",
        prompt_version="v1",
        system_prompt_sha256="x",
        input_sha256="x",
        request_ids=[],
        stop_reason="end_turn",
        input_tokens=1_000_000,
        output_tokens=100_000,
        seconds=1.0,
    )
    assert call_cost(call) == pytest.approx(3.00)


def test_stopped_run_scores_as_wrong_without_crashing():
    result = result_from_truth("claim_002").model_copy(
        update={"coverage": None, "risk": None, "stopped_reason": "no policy record found"}
    )
    s = score(result)
    assert not s.coverage_correct and not s.risk_level
    assert s.stopped and s.flag_precision is None


def test_report_renders(tmp_path):
    from dataclasses import asdict

    from hall_monitor.claims.evals.baseline import write_report
    from hall_monitor.claims.evals.scoring import summarize

    result = result_from_truth("claim_001")
    s = score(result)
    runs = [{"score": asdict(s), "result": result.model_dump(mode="json")}]
    meta = {
        "run_at": "now",
        "git_commit": "abc1234",
        "git_dirty": False,
        "claims": 1,
        "trials": 1,
        "critic": True,
        "agents": [],
    }
    write_report(
        tmp_path / "report.md",
        meta,
        summarize([s]),
        runs,
        {"claim_001": load_ground_truth("claim_001")},
    )
    assert "Runs revised after critic review" in (tmp_path / "report.md").read_text()


@pytest.mark.parametrize(
    ("amount", "text", "found"),
    [
        (100.0, "Total USD 1,100.00", False),
        (100.0, "Paid USD 100.00 today", True),
        (1150.0, "USD 1,150.00", True),
        (1150.0, "USD 1150.00", True),
        (50.0, "USD 150.00", False),
    ],
)
def test_amounts_match_whole_numbers_only(amount, text, found):
    from hall_monitor.claims.evals.scoring import amount_in

    assert amount_in(amount, text) is found


def test_over_citing_lowers_clause_precision_not_recall():
    result = result_from_truth("claim_001")
    result.coverage.applicable_clauses += ["2.2", "2.3", "6.2"]
    s = score(result)
    assert s.clause_recall == 1.0
    assert s.clause_precision == 0.25


# --- Critic seeded-error eval --------------------------------------------------------


def test_seeded_cases_plant_exactly_one_error_each():
    from hall_monitor.claims.evals.critic_seeded import build_cases

    truths = [load_ground_truth(cid) for cid in sorted(CLAIMS)]
    cases = build_cases(truths)
    by_seed = {}
    for case in cases:
        by_seed.setdefault(case.seed, []).append(case)
    assert len(by_seed["control"]) == len(truths)
    for case in by_seed["amount_ignores_cap"]:
        truth = load_ground_truth(case.claim_id)
        assert case.coverage.covered_amount == truth.extraction.total_claimed
        assert case.coverage.covered_amount != truth.coverage.covered_amount
        assert case.risk.flags == truth.risk.flags
    for case in by_seed["unsupported_flag"]:
        assert "amounts_inconsistent" in case.risk.flags
    for case in by_seed["missing_incident_date"]:
        assert case.extraction.incident_date is None


def test_seeded_scoring():
    from hall_monitor.claims.evals.critic_seeded import _control, _missing_date, run_case
    from hall_monitor.claims.gateway import claims_gateway
    from hall_monitor.claims.models import CriticIssue, CriticReview, ReviewedStage
    from hall_monitor.registry import Registry
    from tests.fakes import TEST_REGISTRY_DIR, fake_client

    truth = load_ground_truth("claim_001")
    issue = CriticReview(
        issues=[CriticIssue(stage=ReviewedStage.INTAKE, problem="Date missing.", evidence="x")]
    )
    registry = Registry.from_dir(TEST_REGISTRY_DIR)
    context = {
        "gateway": claims_gateway(registry),
        "wording": WORDING,
        "registry": registry,
        "client": fake_client({CriticReview: issue}),
    }
    assert run_case(_missing_date(truth), CLAIMS["claim_001"], context).caught is True
    assert run_case(_control(truth), CLAIMS["claim_001"], context).false_alarm is True


def test_run_stopped_before_intake_scores_as_wrong_without_crashing():
    result = result_from_truth("claim_002").model_copy(
        update={"extraction": None, "coverage": None, "risk": None, "stopped_reason": "refused"}
    )
    s = score(result)
    assert not any(s.extraction_fields.values())
    assert s.values_checked == 0 and s.stopped


def test_simulated_override_rate_on_a_published_run():
    from pathlib import Path

    from hall_monitor.claims.evals.override import simulate

    summary = simulate(Path("evals/results/hardened-critic"))
    assert summary["routed_to_adjuster"] + summary["routed_automatically"] == 102
    assert summary["overrides"] == 1 and summary["unreviewed_ai_errors"] == 0


@pytest.mark.parametrize(
    ("amount", "text", "found"),
    [
        (1000.0, "Total: $1,000", True),
        (1000.0, "Total 1000 USD", True),
        (1000.0, "Total: 1,000.00", True),
        (1000.0, "Total: 1,000.50", False),
        (100.0, "Total: 1,100", False),
        (100.5, "Total: 100", False),
    ],
)
def test_amounts_are_found_with_or_without_cents(amount, text, found):
    assert amount_in(amount, text) is found


@pytest.mark.parametrize(
    ("reason", "infrastructure"),
    [
        ("APIConnectionError from the model call", True),
        ("unexpected KeyError: 'claude-x'", True),
        ("no policy record found for the policy number on the claim", False),
        ("hall pass refused: agent 'intake' is planned, not active", True),
        ("APIError from the model call; cost unknown: no price for model 'x'", True),
        ("no policy record found; cost unknown: no price for model 'x'", True),
        # Review 46, H5: refused before any model call, so nothing about the model was tested.
        ("spend budgets not approved: budgets.yaml is not the approved version", True),
        ("spend ledger unavailable (OperationalError)", True),
        ("spend cap reached: daily claims cap of 5.00 USD", True),
    ],
)
def test_infrastructure_stops_are_told_apart_from_model_behaviour(reason, infrastructure):
    assert is_infrastructure_stop(reason) is infrastructure


@pytest.mark.parametrize(
    ("amounts", "text", "missing"),
    [
        ([45.0, 45.0], "Ticket: 45.00", [45.0]),
        ([45.0, 45.0], "Ticket: 45.00. Second ticket: $45", []),
        ([100.0], "Total 1,100.00", [100.0]),
        ([], "anything", []),
    ],
)
def test_each_amount_needs_its_own_occurrence(amounts, text, missing):
    assert untraced(amounts, text) == missing


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        (2026.0, "Flight cancelled on 2026-08-14."),
        (14.0, "Flight cancelled on 2026-08-14."),
        (8.0, "Flight cancelled on 2026/08/14."),
        (45.0, "Receipt printed 21:45."),
        (20.0, "Arrived 14:20 local time."),
    ],
)
def test_parts_of_dates_and_times_are_not_amounts(amount, text):
    assert not amount_in(amount, text)


@pytest.mark.parametrize(
    ("amount", "text", "found"),
    [
        (1000.0, "Total: $1,000", True),
        (1000.0, "Total: US$1,000.00", True),
        (1000.0, "Total USD 1,000.00", True),
        (1000.0, "Total 1000 USD", True),
        (1000.0, "Total: 1,000.00", False),
        (1000.0, "Total: EUR 1,000.00", False),
        (1000.0, "Total: 1,000.00 EUR", False),
        (1000.0, "Total: C$1,000.00", False),
        (1000.0, "Total: AUD 1,000.00", False),
        (2026.0, "Flight BA2026 on 14 August 2026 ($310.00).", False),
        (1000.0, "Total: USD $1,000.00", True),
        (1000.0, "TOTAL $1,000.00", True),
        (1000.0, "Total: CAD $1,000.00", False),
        (1000.0, "Total: $1,000.00 CAD", False),
        (1000.0, "Total: AUD $1,000", False),
        (1000.0, "Total: MXN$1,000", False),
        (1000.0, "Total: EUR 1,000 USD", False),
        (1000.0, "Total: €1,000 USD", False),
        (1000.0, "Total: $1,000.00 €", False),
        (20.0, "Discount -$20.00", False),
        (2026.0, "Date: 14 August 2026\nUSD 310.00", False),
        (3.0, "Nights: 3\nUSD 450.00", False),
        (2.0, "Total USD\n2 bags", False),
        (2.0, "Total $\n2 bags", False),
        (450.0, "Fare HK $450.00", False),
        (450.0, "Fare NZ $450.00", False),
        (450.0, "Fare (CAD) $450.00", False),
        (450.0, "Fare CAD: $450.00", False),
        (450.0, "Fare CAD  $450.00", False),
        (450.0, "Fare cad $450.00", False),
        (450.0, "Fare Cdn $450.00", False),
        (450.0, "Fare $450.00 cad", False),
        (450.0, "Fare $450.00 (CAD)", False),
        (450.0, "Fare $450.00  CAD", False),
        (20.0, "Refund \u2212$20.00", False),
        (450.0, "Paid a $450.00 deposit", True),
        (450.0, "Fare: $450.00 (paid by card)", True),
        (450.0, "TOTAL $450.00", True),
        (450.0, "Fare CAD\u00a0$450.00", False),
        (450.0, "Fare $450.00\u00a0CAD", False),
        (450.0, "Fare CAD    $450.00", False),
        (450.0, "Fare CAD. $450.00", False),
        (450.0, "Fare Cdn. $450.00", False),
        (450.0, "Fare CAD, $450.00", False),
        (450.0, "Fare CAD = $450.00", False),
        (450.0, "Fare $450.00 in CAD", False),
        (450.0, "Fare $450.00 (in CAD)", False),
        (450.0, "Fare $450.00 Canadian", False),
        # Ordinary US receipts mustn't be sent to a person by the currency check.
        (45.0, "Taxi - $45.00", True),
        (45.0, "Taxi \u2013 $45.00", True),
        (450.0, "Acme Travel Co $450.00", True),
        (450.0, "Denver, CO $450.00", True),
        (450.0, "San Diego, CA $450.00", True),
        (450.0, "Fee (ca) $450.00", True),
        (450.0, "$450.00 co-pay", True),
        (450.0, "$450.00 AR", True),
        # Accounting negatives are refunds, like a minus sign (review finding 8).
        (200.0, "Refund ($200.00)", False),
        (200.0, "Refund ( $200.00 )", False),
        (200.0, "Refund (USD 200.00)", False),
        (200.0, "Refund (200.00 USD)", False),
        (150.0, "$150.00 CR", False),
        (150.0, "150.00 USD CR", False),
        (150.0, "$150.00 cr", False),
        (200.0, "Hotel (2 nights) $200.00", True),
        (200.0, "Hotel $200.00 (2 nights)", True),
        (150.0, "$150.00 CRUISE", True),
    ],
)
def test_the_routing_trace_only_counts_dollar_amounts(amount, text, found):
    """Routing pays in USD, so it traces only figures the documents mark as dollars: a bare
    number (a year, a booking reference) or another currency's figure doesn't count."""
    assert amount_in(amount, text, usd=True) is found
    assert (untraced([amount], text, usd=True) == []) is found


@pytest.mark.parametrize(
    "text, named",
    [
        ("INVOICE\nAll amounts in CAD\nTaxi $45.00", ["CAD"]),
        ("Hotel total $300.00 (prices shown in euros)", ["EUR"]),
        ("Receipt — currency: GBP\nDinner $80.00", ["GBP"]),
        ("Fare C$120.00", ["CAD"]),
        ("Room ¥12,000", ["JPY"]),
        ("Paid in Mexican pesos: $1,200.00", ["MXN"]),
        ("Two amounts: EUR 40 and AUD 60", ["AUD", "EUR"]),
    ],
)
def test_another_currency_named_anywhere_in_the_documents_is_found(text, named):
    from hall_monitor.claims.amounts import other_currencies

    assert other_currencies(text) == named


@pytest.mark.parametrize(
    "text",
    [
        "Taxi $45.00 ATM fee $3.00, VAT $9.00",
        "ALL AMOUNTS IN US DOLLARS\nHotel $300.00",
    ],
)
def test_ordinary_us_receipts_name_no_other_currency(text):
    from hall_monitor.claims.amounts import other_currencies

    assert other_currencies(text) == []


def test_a_claim_whose_documents_name_another_currency_goes_to_a_person():
    auto = next(
        cid for cid in sorted(CLAIMS) if result_from_truth(cid).routing.route.value == "auto"
    )
    truth = load_ground_truth(auto)
    claim = redact_claim(CLAIMS[auto])[0]
    routing = route_claim(
        truth.extraction,
        CoverageAssessment(**truth.coverage.model_dump()),
        RiskAssessment(**truth.risk.model_dump(), reasoning=""),
        Recommendation(next_step=truth.next_step, reasons=[]),
        policy=load_policy_records().get(truth.extraction.policy_number),
        # Every figure written with a bare "$", under a header naming another currency.
        claim_text="All amounts in this claim are in CAD\n"
        + re.sub(r"\bUSD\b\s?|\bUS\$", "$", source_text(claim)),
    )
    # The auto claims' receipts use a bare "$" (or name another currency already, marked USD);
    # under a CAD header a bare "$" no longer counts.
    assert routing.route.value != "auto"
    assert any(
        r.startswith("claimed amounts not in the claim documents marked USD (they name CAD)")
        for r in routing.reasons
    ), routing.reasons


def test_figures_marked_usd_still_trace_when_another_currency_is_named():
    from hall_monitor.claims.amounts import untraced

    text = "Total: USD 38.50 (card, converted from JPY)\nShirt US$214.00"
    assert untraced([38.5, 214.0], text, usd=True, strict=True) == []
    assert untraced([38.5], "Total: $38.50 (all amounts in CAD)", usd=True, strict=True) == [38.5]


# --- Review 38b: parentheses and currency words in ordinary narrative ----------------------------


@pytest.mark.parametrize(
    ("amount", "text", "found"),
    [
        (480.0, "I bought a suit ($480.00), dress shoes ($220.00) and a tie.", True),
        (220.0, "I bought a suit ($480.00), dress shoes ($220.00) and a tie.", True),
        (480.0, "Replacement suit ($480.00)\r\nShoes $220.00", False),  # a ledger value alone
        (480.0, "Suit ($480.00) refunded by the store", False),
        (480.0, "Store credit: suit ($480.00), shoes", False),
        (480.0, "Returned items: suit ($480.00), shoes", False),
    ],
)
def test_an_item_listed_in_parentheses_in_the_narrative_still_traces(amount, text, found):
    """A figure in parentheses is an accounting negative only when it stands alone at the end
    of its line, or a refund, credit or return word is on the line: a narrative listing items
    with their prices in brackets still traces."""
    assert amount_in(amount, text, usd=True) is found


@pytest.mark.parametrize(
    "text",
    [
        "The airline says my bag won't be found.\nReplacement jacket $120.00",
        "Stayed two nights at the Sterling Hotel, Denver.\nRoom total $300.00",
        "Euro rail pass, unused because of the injury\nPass price $300.00",
        "Rand Hughes, the tour guide, confirmed the cancellation.\nTour fee $300.00",
        "Item: BLUE PEN, pack of 2\nStationery total $300.00",
    ],
)
def test_wont_and_hotel_names_dont_make_a_us_claim_strict(text):
    """Words that are also currency names, or English words that are also currency codes, away
    from any figure and not in a header phrase, don't make the documents name another
    currency, so a bare "$" still counts as US dollars."""
    from hall_monitor.claims.amounts import other_currencies, untraced

    assert other_currencies(text) == []
    strict = bool(other_currencies(text))
    amount = 120.0 if "jacket" in text else 300.0
    assert untraced([amount], text, usd=True, strict=strict) == []


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("Paid 45,000 Korean won for the taxi", ["KRW"]),
        ("Safari deposit in South African rand: $300.00", ["ZAR"]),
        ("Taxi PEN 45.00", ["PEN"]),
        ("Room 300 SAR", ["SAR"]),
        ("Room 200 euros", ["EUR"]),
        ("Currency: sterling\nRoom $300.00", ["GBP"]),
    ],
)
def test_currency_names_next_to_a_figure_or_in_a_header_are_still_found(text, named):
    from hall_monitor.claims.amounts import other_currencies

    assert other_currencies(text) == named
