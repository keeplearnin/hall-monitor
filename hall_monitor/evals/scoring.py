"""Deterministic scoring of a triage result against ground truth. No LLM judge.

Definitions (also printed in every report):
- Extraction accuracy: share of extraction fields that match ground truth.
- Coverage-reasoning accuracy: share of runs where the coverage decision is right, the covered
  amount is within 1 USD, and every deciding clause in the ground truth is cited.
- Hallucination rate: share of checked values that can't be traced to the source: an extracted
  name, policy number, location or amount that doesn't appear in the claim, a document that
  wasn't attached, or a cited clause that doesn't exist in the policy wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hall_monitor.amounts import amount_in, source_text  # noqa: F401 (re-exported)
from hall_monitor.models import ClaimSubmission, GroundTruth, IntakeExtraction, TriageResult

EXTRACTION_FIELDS = (
    "claimant_name",
    "policy_number",
    "claim_type",
    "incident_date",
    "incident_location",
    "claimed_items",
    "total_claimed",
    "currency",
    "supporting_documents",
)

# Words too generic to identify a place on their own.
GENERIC_PLACE_WORDS = {"airport", "hospital", "clinic", "city", "the", "near", "and", "of"}


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower().replace("'s", "")) if len(t) > 1}


def _place_tokens(text: str) -> set[str]:
    return _tokens(text) - GENERIC_PLACE_WORDS


def location_matches(predicted: str | None, expected: str | None) -> bool:
    """Both null, or the prediction names at least one of the expected place's words."""
    if expected is None or predicted is None:
        return predicted is None and expected is None
    return bool(_place_tokens(predicted) & _place_tokens(expected))


def score_extraction(predicted: IntakeExtraction, expected: IntakeExtraction) -> dict[str, bool]:
    def amounts(e: IntakeExtraction) -> list[float]:
        return sorted(round(i.amount, 2) for i in e.claimed_items)

    return {
        "claimant_name": _norm(predicted.claimant_name) == _norm(expected.claimant_name),
        "policy_number": (predicted.policy_number or "").strip().upper()
        == (expected.policy_number or "").strip().upper(),
        "claim_type": predicted.claim_type == expected.claim_type,
        "incident_date": predicted.incident_date == expected.incident_date,
        "incident_location": location_matches(
            predicted.incident_location, expected.incident_location
        ),
        "claimed_items": amounts(predicted) == amounts(expected),
        "total_claimed": abs(predicted.total_claimed - expected.total_claimed) < 0.01,
        "currency": predicted.currency.strip().upper() == expected.currency.strip().upper(),
        "supporting_documents": set(predicted.supporting_documents)
        == set(expected.supporting_documents),
    }


def normalize_clause(clause: str) -> str:
    """'2.1(a)', 'Section 2.1' and '2.1' all become '2.1'."""
    match = re.search(r"\d+\.\d+", clause)
    return match.group(0) if match else clause.strip()


def clause_exists(clause: str, wording: str) -> bool:
    return re.search(rf"^{re.escape(clause)}\s", wording, re.MULTILINE) is not None


def is_infrastructure_stop(reason: str | None) -> bool:
    """A stop that says nothing about the model: an API failure (outage, rate limit, no
    credit), an unexpected error in our own code, a refused hall pass or a model with no price
    (our configuration). Evals count these as errors, so none can pass as a safely stopped claim.
    """
    return bool(reason) and (
        "from the model call" in reason
        or "cost unknown:" in reason
        or reason.startswith(("unexpected ", "hall pass refused"))
    )


def unsupported_values(
    result: TriageResult, claim: ClaimSubmission, wording: str
) -> tuple[int, list[str]]:
    """Return how many values were checked and which ones aren't traceable to a source."""
    extraction = result.extraction
    if extraction is None:  # stopped before intake finished: nothing extracted to check
        return 0, []
    source = source_text(claim)
    source_tokens = _tokens(source)
    checks: list[tuple[str, bool]] = [
        (
            f"claimant_name={extraction.claimant_name}",
            _norm(extraction.claimant_name) in _norm(source),
        ),
    ]
    if extraction.policy_number:
        checks.append(
            (f"policy_number={extraction.policy_number}", extraction.policy_number in source)
        )
    if extraction.incident_location:
        place = _place_tokens(extraction.incident_location)
        checks.append(
            (f"incident_location={extraction.incident_location}", bool(place & source_tokens))
        )
    for item in extraction.claimed_items:
        checks.append((f"amount={item.amount:.2f}", amount_in(item.amount, source)))
    attached = {a.filename for a in claim.attachments}
    for doc in extraction.supporting_documents:
        checks.append((f"document={doc}", doc in attached))
    for clause in result.coverage.applicable_clauses if result.coverage else []:
        checks.append((f"clause={clause}", clause_exists(normalize_clause(clause), wording)))
    return len(checks), [label for label, ok in checks if not ok]


@dataclass
class RunScore:
    claim_id: str
    trial: int
    error: str | None = None
    extraction_fields: dict[str, bool] = field(default_factory=dict)
    coverage_decision: bool = False
    covered_amount: bool = False
    clause_recall: float = 0.0
    clause_precision: float | None = None
    coverage_correct: bool = False
    risk_level: bool = False
    flag_precision: float | None = None
    flag_recall: float | None = None
    next_step: bool = False
    route: bool = False
    values_checked: int = 0
    unsupported: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    seconds: float = 0.0
    revisions: int = 0
    critic_reviews: int = 0
    critic_unresolved: bool = False
    stopped: bool = False
    tool_calls: int = 0
    refused_tool_calls: int = 0
    coverage_looked_up_policy: bool | None = None


def score_run(
    result: TriageResult,
    truth: GroundTruth,
    claim: ClaimSubmission,
    wording: str,
    *,
    trial: int,
    cost_usd: float,
) -> RunScore:
    coverage, risk = result.coverage, result.risk
    expected_clauses = {normalize_clause(c) for c in truth.coverage.applicable_clauses}
    cited = {normalize_clause(c) for c in coverage.applicable_clauses} if coverage else set()
    clause_recall = (
        len(expected_clauses & cited) / len(expected_clauses) if expected_clauses else 1.0
    )
    clause_precision = len(expected_clauses & cited) / len(cited) if cited else None

    decision_ok = coverage is not None and coverage.decision == truth.coverage.decision
    amount_ok = (
        coverage is not None and abs(coverage.covered_amount - truth.coverage.covered_amount) <= 1.0
    )

    expected_flags = set(truth.risk.flags)
    flagged = set(risk.flags) if risk else set()
    flag_precision = len(expected_flags & flagged) / len(flagged) if flagged else None
    flag_recall = len(expected_flags & flagged) / len(expected_flags) if expected_flags else None

    checked, unsupported = unsupported_values(result, claim, wording)
    return RunScore(
        claim_id=claim.claim_id,
        trial=trial,
        extraction_fields=(
            score_extraction(result.extraction, truth.extraction)
            if result.extraction is not None
            else {f: False for f in EXTRACTION_FIELDS}
        ),
        coverage_decision=decision_ok,
        covered_amount=amount_ok,
        clause_recall=clause_recall,
        clause_precision=clause_precision,
        coverage_correct=decision_ok and amount_ok and clause_recall == 1.0,
        risk_level=risk is not None and risk.level == truth.risk.level,
        flag_precision=flag_precision,
        flag_recall=flag_recall,
        next_step=result.recommendation.next_step == truth.next_step,
        route=result.routing.route == truth.route,
        values_checked=checked,
        unsupported=unsupported,
        cost_usd=cost_usd,
        seconds=sum(c.seconds for c in result.agent_calls),
        revisions=result.revisions,
        critic_reviews=len(result.critic_reviews),
        critic_unresolved=result.critic_unresolved,
        stopped=result.stopped_reason is not None,
        tool_calls=len(result.tool_calls),
        refused_tool_calls=sum(not t.allowed for t in result.tool_calls),
        coverage_looked_up_policy=(
            any(
                t.actor == "coverage_check" and t.tool == "policy_lookup" for t in result.tool_calls
            )
            if result.coverage is not None
            else None
        ),
    )


def failed_run(claim_id: str, trial: int, error: str) -> RunScore:
    """A run that errored scores zero on everything."""
    return RunScore(
        claim_id=claim_id,
        trial=trial,
        error=error,
        extraction_fields={f: False for f in EXTRACTION_FIELDS},
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(scores: list[RunScore]) -> dict[str, float | int | None]:
    fields = [ok for s in scores for ok in s.extraction_fields.values()]
    checked = sum(s.values_checked for s in scores)
    ok_runs = [s for s in scores if s.error is None]
    return {
        "runs": len(scores),
        "errors": len(scores) - len(ok_runs),
        "extraction_accuracy": _mean([float(f) for f in fields]),
        "coverage_reasoning_accuracy": _mean([float(s.coverage_correct) for s in scores]),
        "coverage_decision_accuracy": _mean([float(s.coverage_decision) for s in scores]),
        "covered_amount_accuracy": _mean([float(s.covered_amount) for s in scores]),
        "clause_recall": _mean([s.clause_recall for s in scores]),
        "clause_precision": _mean(
            [s.clause_precision for s in scores if s.clause_precision is not None]
        ),
        "risk_level_accuracy": _mean([float(s.risk_level) for s in scores]),
        "flag_precision": _mean([s.flag_precision for s in scores if s.flag_precision is not None]),
        "flag_recall": _mean([s.flag_recall for s in scores if s.flag_recall is not None]),
        "next_step_accuracy": _mean([float(s.next_step) for s in scores]),
        "route_accuracy": _mean([float(s.route) for s in scores]),
        "hallucination_rate": (
            sum(len(s.unsupported) for s in scores) / checked if checked else None
        ),
        "cost_per_claim_usd": _mean([s.cost_usd for s in ok_runs]),
        "seconds_per_claim": _mean([s.seconds for s in ok_runs]),
        "revised_rate": _mean([float(s.revisions > 0) for s in ok_runs]),
        "critic_unresolved_rate": _mean([float(s.critic_unresolved) for s in ok_runs]),
        "stopped_rate": _mean([float(s.stopped) for s in ok_runs]),
        "tool_calls_per_claim": _mean([float(s.tool_calls) for s in ok_runs]),
        "refused_tool_calls": sum(s.refused_tool_calls for s in ok_runs),
        "coverage_policy_lookup_rate": _mean(
            [
                float(s.coverage_looked_up_policy)
                for s in ok_runs
                if s.coverage_looked_up_policy is not None
            ]
        ),
    }
