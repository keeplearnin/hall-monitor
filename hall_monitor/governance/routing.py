"""Who decides: the routing rule is code, not a model, so it can't be talked out of a review.

It checks the facts behind a decision, not just the labels the agents put on them: a claim
the coverage step pays nothing on is a denial whatever the recommendation says, and a payout
above the amount claimed is wrong whatever the coverage step says. Anything other than a
clean, low-risk payment within bounds goes to an adjuster.
"""

from __future__ import annotations

from hall_monitor.governance import ADJUSTER_PAYOUT_THRESHOLD_USD
from hall_monitor.models import (
    CoverageAssessment,
    CoverageDecision,
    IntakeExtraction,
    NextStep,
    Recommendation,
    RiskAssessment,
    RiskLevel,
    Route,
    RoutingDecision,
)


def route_claim(
    extraction: IntakeExtraction,
    coverage: CoverageAssessment,
    risk: RiskAssessment,
    recommendation: Recommendation,
    *,
    critic_unresolved: bool = False,
    critic_changed: list[str] | None = None,
) -> RoutingDecision:
    reasons = []
    if recommendation.next_step is not NextStep.PAY:
        reasons.append(f"recommended {recommendation.next_step.value.replace('_', ' ')}")
    if coverage.decision is CoverageDecision.NOT_COVERED:
        reasons.append("nothing covered (a denial)")
    if coverage.covered_amount > extraction.total_claimed + 0.005:
        reasons.append(
            f"payout {coverage.covered_amount:,.2f} exceeds the "
            f"{extraction.total_claimed:,.2f} claimed"
        )
    if coverage.covered_amount > ADJUSTER_PAYOUT_THRESHOLD_USD:
        amount, threshold = coverage.covered_amount, ADJUSTER_PAYOUT_THRESHOLD_USD
        reasons.append(f"payout {amount:,.2f} above {threshold:,} threshold")
    if risk.flags:
        reasons.append("fraud flags: " + ", ".join(risk.flags))
    if risk.level is not RiskLevel.LOW:
        reasons.append(f"{risk.level.value} risk")
    if critic_unresolved:
        reasons.append("critic still disagrees after revisions")
    if critic_changed:
        reasons.append("critic changed " + ", ".join(critic_changed))
    return RoutingDecision(route=Route.ADJUSTER if reasons else Route.AUTO, reasons=reasons)


def route_stopped(reason: str) -> RoutingDecision:
    """A claim the pipeline couldn't finish always goes to a person."""
    return RoutingDecision(route=Route.ADJUSTER, reasons=[f"pipeline stopped: {reason}"])
