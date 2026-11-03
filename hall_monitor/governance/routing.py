"""Who decides: the routing rule is code, not a model, so it can't be talked out of a review."""

from __future__ import annotations

from hall_monitor.governance import ADJUSTER_PAYOUT_THRESHOLD_USD
from hall_monitor.models import (
    CoverageAssessment,
    NextStep,
    Recommendation,
    RiskAssessment,
    Route,
    RoutingDecision,
)


def route_claim(
    coverage: CoverageAssessment, risk: RiskAssessment, recommendation: Recommendation
) -> RoutingDecision:
    reasons = []
    if recommendation.next_step is NextStep.DENY:
        reasons.append("recommended denial")
    if recommendation.next_step is NextStep.INVESTIGATE:
        reasons.append("recommended investigation")
    if coverage.covered_amount > ADJUSTER_PAYOUT_THRESHOLD_USD:
        amount, threshold = coverage.covered_amount, ADJUSTER_PAYOUT_THRESHOLD_USD
        reasons.append(f"payout {amount:,.2f} above {threshold:,} threshold")
    if risk.flags:
        reasons.append("fraud flags: " + ", ".join(risk.flags))
    return RoutingDecision(route=Route.ADJUSTER if reasons else Route.AUTO, reasons=reasons)
