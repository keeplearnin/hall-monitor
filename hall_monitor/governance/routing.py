"""Who decides: the routing rule is code, not a model, so it can't be talked out of a review.

It checks the facts behind a decision, not just the labels the agents put on them: a claim
the coverage step pays nothing on is a denial whatever the recommendation says, and a payout
above the amount claimed is wrong whatever the coverage step says. Facts the agents could get
wrong are checked in code against the policy record and the claim documents: the claimant must
be the insured, the incident must fall inside the coverage period, every claimed amount must
appear in the documents and the items must add up to the total, and amounts must be in USD (the
policy pays in USD and nothing here converts currency). The amount check traces an agent's
figures to what the claimant sent; it doesn't verify the claimant's own figures. Anything other
than a clean, low-risk payment within bounds goes to an adjuster.
"""

from __future__ import annotations

from hall_monitor.amounts import amount_in
from hall_monitor.governance import ADJUSTER_PAYOUT_THRESHOLD_USD
from hall_monitor.models import (
    CoverageAssessment,
    CoverageDecision,
    IntakeExtraction,
    NextStep,
    PolicyRecord,
    Recommendation,
    RiskAssessment,
    RiskLevel,
    Route,
    RoutingDecision,
)
from hall_monitor.names import name_key


def route_claim(
    extraction: IntakeExtraction,
    coverage: CoverageAssessment,
    risk: RiskAssessment,
    recommendation: Recommendation,
    *,
    critic_unresolved: bool = False,
    critic_changed: list[str] | None = None,
    injection_signals: list[str] | None = None,
    policy: PolicyRecord | None = None,
    claim_text: str | None = None,
) -> RoutingDecision:
    """claim_text is everything the claimant sent (amounts.source_text); without it the amounts
    can't be traced, and the claim goes to a person."""
    reasons = []
    if extraction.currency.strip().upper() != "USD":
        reasons.append(f"claim is in {extraction.currency}, not USD: amounts can't be compared")
    if policy is None:
        reasons.append("no policy record to check the claim against")
    else:
        if name_key(extraction.claimant_name) != name_key(policy.insured_name):
            reasons.append("claimant is not the insured named on the policy")
        if extraction.incident_date is None:
            reasons.append("no incident date to check against the coverage period")
        elif not policy.coverage_start <= extraction.incident_date <= policy.coverage_end:
            reasons.append("incident date is outside the coverage period")
    if claim_text is None:
        reasons.append("no claim text to check the claimed amounts against")
    else:
        untraced = [
            i.amount for i in extraction.claimed_items if not amount_in(i.amount, claim_text)
        ]
        if untraced:
            listed = ", ".join(f"{a:,.2f}" for a in untraced)
            reasons.append(f"claimed amounts not in the claim documents: {listed}")
    items_total = sum(i.amount for i in extraction.claimed_items)
    if abs(items_total - extraction.total_claimed) > 0.005:
        reasons.append(
            f"claimed items ({items_total:,.2f}) don't add up to the "
            f"{extraction.total_claimed:,.2f} claimed"
        )
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
    if injection_signals:
        reasons.append("claim text addresses the AI: " + ", ".join(injection_signals))
    return RoutingDecision(route=Route.ADJUSTER if reasons else Route.AUTO, reasons=reasons)


def route_stopped(reason: str) -> RoutingDecision:
    """A claim the pipeline couldn't finish always goes to a person."""
    return RoutingDecision(route=Route.ADJUSTER, reasons=[f"pipeline stopped: {reason}"])
