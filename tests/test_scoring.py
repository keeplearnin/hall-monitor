"""The eval's scoring rules: ground truth scores perfectly, and known errors are caught."""

import pytest

from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.evals.cost import call_cost
from hall_monitor.evals.scoring import (
    clause_exists,
    failed_run,
    location_matches,
    normalize_clause,
    score_run,
    summarize,
)
from hall_monitor.governance.routing import route_claim
from hall_monitor.models import (
    AgentCall,
    ClaimedItem,
    CoverageAssessment,
    Recommendation,
    RiskAssessment,
    TriageResult,
)

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
        routing=route_claim(truth.extraction, coverage, risk, recommendation),
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
        request_id=None,
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

    from hall_monitor.evals.baseline import write_report
    from hall_monitor.evals.scoring import summarize

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
    from hall_monitor.evals.scoring import amount_in

    assert amount_in(amount, text) is found


def test_over_citing_lowers_clause_precision_not_recall():
    result = result_from_truth("claim_001")
    result.coverage.applicable_clauses += ["2.2", "2.3", "6.2"]
    s = score(result)
    assert s.clause_recall == 1.0
    assert s.clause_precision == 0.25
