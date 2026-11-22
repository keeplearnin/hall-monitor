"""Who decides: the routing rule is code, not a model, so it can't be talked out of a review.

It checks the facts behind a decision, not just the labels the agents put on them: a claim
the coverage step pays nothing on is a denial whatever the recommendation says, and a payout
above the amount claimed is wrong whatever the coverage step says. Facts the agents could get
wrong are checked in code against the policy record and the claim documents: the claimant must
be the insured, and the documents must name both the claimant and the policy number; the
incident must fall inside the coverage period (the date itself is the intake's); every claimed
amount must appear in the documents and the items must add up to the total; and amounts must be
in USD (the policy pays in USD and nothing here converts currency). The amount check traces an
agent's figures to what the claimant sent: each item to its own occurrence in the documents, and the
total too, counting only figures the documents mark as dollars, so the USD check rests on the
documents and not just the intake's label. It doesn't verify the claimant's own figures.
Anything other than a clean, low-risk payment within bounds goes to an adjuster.
"""

from __future__ import annotations

from hall_monitor.claims.amounts import amount_in, other_currencies, untraced
from hall_monitor.claims.models import (
    POLICY_NUMBER_PATTERN,
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
from hall_monitor.shared_models import ToolCall

# Payouts above this amount stop for an adjuster. See data/synthetic/README.md.
ADJUSTER_PAYOUT_THRESHOLD_USD = 2_500
# How the reasons that the claimed figures aren't supported by the documents begin: the adjuster
# limits read them (claimed_total_traced) and mustn't trust such a total.
UNTRACED_AMOUNTS = "claimed amounts not in the claim documents"
ITEMS_DONT_ADD_UP = "claimed items"


def route_claim(
    extraction: IntakeExtraction,
    coverage: CoverageAssessment,
    risk: RiskAssessment,
    recommendation: Recommendation,
    *,
    critic_unresolved: bool = False,
    critic_changed: list[str] | None = None,
    injection_signals: list[str] | None = None,
    tool_result_signals: list[str] | None = None,
    policy: PolicyRecord | None = None,
    claim_text: str | None = None,
) -> RoutingDecision:
    """claim_text is everything the claimant sent (amounts.source_text); without it the amounts
    can't be traced, and the claim goes to a person. tool_result_signals are those of every
    tool result that addressed the AI (tool_result_signals(tool_calls)): held back from an
    agent, or returned to the pipeline's own code, which reads it as data."""
    reasons = []
    if extraction.currency.strip().upper() != "USD":
        reasons.append(f"claim is in {extraction.currency}, not USD: amounts can't be compared")
    if policy is None:
        reasons.append("no policy record to check the claim against")
    else:
        if name_key(extraction.claimant_name) != name_key(policy.insured_name):
            reasons.append("claimant is not the insured named on the policy")
        # Review 46: the policy number and the claimant are the intake's word until the claim
        # itself names them; otherwise an agent could name another customer's policy and its
        # insured and the claim would pay automatically.
        if claim_text is not None:
            if policy.policy_number not in POLICY_NUMBER_PATTERN.findall(claim_text.upper()):
                reasons.append("the policy number isn't in the claim documents")
            if f" {name_key(extraction.claimant_name)} " not in f" {name_key(claim_text)} ":
                reasons.append("the claimant's name isn't in the claim documents")
        if extraction.incident_date is None:
            reasons.append("no incident date to check against the coverage period")
        elif not policy.coverage_start <= extraction.incident_date <= policy.coverage_end:
            reasons.append("incident date is outside the coverage period")
    if claim_text is None:
        reasons.append("no claim text to check the claimed amounts against")
    else:
        # Where the documents name another currency anywhere, a bare "$" may be it: only figures
        # marked USD or US$ count then.
        named = other_currencies(claim_text)
        strict = bool(named)
        items = [i.amount for i in extraction.claimed_items]
        missing = untraced(items, claim_text, usd=True, strict=strict)
        # Strict, a total written with a bare "$" is still supported when every item is marked
        # USD and the items add up to it (checked below).
        sums_to_total = items and abs(sum(items) - extraction.total_claimed) <= 0.005
        total_ok = amount_in(extraction.total_claimed, claim_text, usd=True, strict=strict) or (
            strict and not missing and sums_to_total
        )
        if not total_ok:
            missing.append(extraction.total_claimed)
        if missing and strict:
            listed = ", ".join(f"{a:,.2f}" for a in missing)
            reasons.append(
                f"{UNTRACED_AMOUNTS} marked USD (they name {', '.join(named)}): {listed}"
            )
        elif missing:
            listed = ", ".join(f"{a:,.2f}" for a in missing)
            reasons.append(f"{UNTRACED_AMOUNTS} in USD: {listed}")
    items_total = sum(i.amount for i in extraction.claimed_items)
    if abs(items_total - extraction.total_claimed) > 0.005:
        reasons.append(
            f"{ITEMS_DONT_ADD_UP} ({items_total:,.2f}) don't add up to the "
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
    if tool_result_signals:
        reasons.append(tool_result_reason(tool_result_signals))
    return RoutingDecision(route=Route.ADJUSTER if reasons else Route.AUTO, reasons=reasons)


def claimed_total_traced(routing: RoutingDecision, stopped: bool) -> bool:
    """Whether the routing rule traced the claimed total to the documents: it ran (the claim
    didn't stop first) and raised neither an untraced amount nor items that don't add up. Until
    then the total is only the intake agent's reading (Review 46)."""
    return not stopped and not any(
        r.startswith((UNTRACED_AMOUNTS, ITEMS_DONT_ADD_UP)) for r in routing.reasons
    )


def tool_result_signals(tool_calls: list[ToolCall]) -> list[str]:
    """The injection signals of every tool result in the claim's log, each once, in order."""
    return list(dict.fromkeys(s for call in tool_calls for s in call.injection_signals))


def tool_result_reason(signals: list[str]) -> str:
    return "a tool result addresses the AI: " + ", ".join(signals)


DUPLICATE_DOCUMENTS = (
    "duplicate_documents: the same documents were filed with an earlier claim on this policy"
)


def route_duplicate_documents(routing: RoutingDecision) -> RoutingDecision:
    """A claim whose documents match an earlier claim's on the same policy (the audit log's
    keyed hash of them, governance/audit.py) goes to a person, whatever the agents found: the
    same receipts filed under a new claim ID mustn't be paid automatically twice."""
    if DUPLICATE_DOCUMENTS in routing.reasons:
        return routing
    return RoutingDecision(route=Route.ADJUSTER, reasons=[*routing.reasons, DUPLICATE_DOCUMENTS])


def route_stopped(reason: str, tool_result_signals: list[str] | None = None) -> RoutingDecision:
    """A claim the pipeline couldn't finish always goes to a person. tool_result_signals, as
    for route_claim: the adjuster is told the records the agents read addressed the AI."""
    reasons = [f"pipeline stopped: {reason}"]
    if tool_result_signals:
        reasons.append(tool_result_reason(tool_result_signals))
    return RoutingDecision(route=Route.ADJUSTER, reasons=reasons)
