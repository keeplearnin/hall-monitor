"""Claim triage: intake, policy lookup, coverage check, risk and fraud, critic, recommendation.

The critic reviews intake, coverage and risk against the claim documents and sends work back
for correction, up to MAX_REVISIONS times. Disagreement that survives the revisions goes to an
adjuster. A claim whose policy can't be found stops and goes to an adjuster.

No MCP tools yet (milestone 4): the policy record and wording are passed in directly.
"""

from __future__ import annotations

from collections import defaultdict

import anthropic
from pydantic import BaseModel

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRun, render_feedback
from hall_monitor.agents.coverage_check import run_coverage_check
from hall_monitor.agents.critic import run_critic
from hall_monitor.agents.intake import run_intake
from hall_monitor.agents.recommendation import run_recommendation
from hall_monitor.agents.risk_fraud import run_risk_fraud
from hall_monitor.governance.routing import route_claim, route_stopped
from hall_monitor.models import (
    AgentCall,
    ClaimSubmission,
    CriticReview,
    IntakeExtraction,
    NextStep,
    PolicyRecord,
    Recommendation,
    ReviewedStage,
    TriageResult,
)
from hall_monitor.registry import Registry

MAX_REVISIONS = 2


def _lookup(extraction: IntakeExtraction, policies: dict[str, PolicyRecord]) -> PolicyRecord | None:
    return policies.get((extraction.policy_number or "").strip().upper())


def _problems(review: CriticReview) -> dict[ReviewedStage, list[str]]:
    by_stage: dict[ReviewedStage, list[str]] = defaultdict(list)
    for issue in review.issues:
        by_stage[issue.stage].append(f"{issue.problem} Evidence: {issue.evidence}")
    return by_stage


def _feedback(
    problems: dict[ReviewedStage, list[str]], stage: ReviewedStage, previous: BaseModel
) -> str | None:
    return render_feedback(previous, problems[stage]) if stage in problems else None


@tracing.observe(name="triage", as_type="chain")
def triage(
    claim: ClaimSubmission,
    *,
    registry: Registry,
    policies: dict[str, PolicyRecord],
    wording: str,
    client: anthropic.Anthropic | None = None,
    critic: bool = True,
) -> TriageResult:
    client = client or anthropic.Anthropic()
    calls: list[AgentCall] = []

    def output[T](run: AgentRun[T]) -> T:
        calls.append(run.call)
        return run.output

    def stopped(extraction: IntakeExtraction, reason: str, **extra) -> TriageResult:
        return TriageResult(
            claim_id=claim.claim_id,
            extraction=extraction,
            coverage=None,
            risk=None,
            recommendation=Recommendation(
                next_step=NextStep.REQUEST_INFORMATION, reasons=[reason.capitalize() + "."]
            ),
            routing=route_stopped(reason),
            agent_calls=calls,
            stopped_reason=reason,
            **extra,
        )

    # Intake, then a code check that the policy exists. One retry with the problem spelled out.
    extraction = output(run_intake(claim, registry, client))
    policy = _lookup(extraction, policies)
    if policy is None:
        problem = (
            f"No policy record matches policy_number {extraction.policy_number!r}. Find the "
            "policy number in the claim and copy it exactly."
        )
        extraction = output(
            run_intake(claim, registry, client, feedback=render_feedback(extraction, [problem]))
        )
        policy = _lookup(extraction, policies)
    if policy is None:
        return stopped(extraction, "no policy record found for the policy number on the claim")

    coverage = output(run_coverage_check(claim, extraction, policy, wording, registry, client))
    risk = output(run_risk_fraud(claim, extraction, policy, registry, client))

    reviews: list[CriticReview] = []
    revisions = 0
    unresolved = False
    while critic:
        review = output(
            run_critic(claim, extraction, policy, coverage, risk, wording, registry, client)
        )
        reviews.append(review)
        if not review.issues:
            break
        if revisions == MAX_REVISIONS:
            unresolved = True
            break
        revisions += 1
        problems = _problems(review)

        if ReviewedStage.INTAKE in problems:
            extraction = output(
                run_intake(
                    claim,
                    registry,
                    client,
                    feedback=_feedback(problems, ReviewedStage.INTAKE, extraction),
                )
            )
            policy = _lookup(extraction, policies)
            if policy is None:
                return stopped(
                    extraction,
                    "no policy record found after the critic's correction",
                    critic_reviews=reviews,
                    revisions=revisions,
                )
            # Coverage and risk depend on the extraction, so both run again.
            coverage = output(
                run_coverage_check(
                    claim,
                    extraction,
                    policy,
                    wording,
                    registry,
                    client,
                    feedback=_feedback(problems, ReviewedStage.COVERAGE_CHECK, coverage),
                )
            )
            risk = output(
                run_risk_fraud(
                    claim,
                    extraction,
                    policy,
                    registry,
                    client,
                    feedback=_feedback(problems, ReviewedStage.RISK_FRAUD, risk),
                )
            )
            continue
        if ReviewedStage.COVERAGE_CHECK in problems:
            coverage = output(
                run_coverage_check(
                    claim,
                    extraction,
                    policy,
                    wording,
                    registry,
                    client,
                    feedback=_feedback(problems, ReviewedStage.COVERAGE_CHECK, coverage),
                )
            )
        if ReviewedStage.RISK_FRAUD in problems:
            risk = output(
                run_risk_fraud(
                    claim,
                    extraction,
                    policy,
                    registry,
                    client,
                    feedback=_feedback(problems, ReviewedStage.RISK_FRAUD, risk),
                )
            )

    recommendation = output(
        run_recommendation(claim.claim_id, extraction, coverage, risk, registry, client)
    )
    return TriageResult(
        claim_id=claim.claim_id,
        extraction=extraction,
        coverage=coverage,
        risk=risk,
        recommendation=recommendation,
        routing=route_claim(coverage, risk, recommendation, critic_unresolved=unresolved),
        agent_calls=calls,
        critic_reviews=reviews,
        revisions=revisions,
        critic_unresolved=unresolved,
    )
