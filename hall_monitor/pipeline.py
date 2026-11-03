"""Single-pass triage: intake, coverage check, risk and fraud, recommendation, routing.

No critic loop yet (milestone 3) and no MCP tools (milestone 4): the policy record and wording
are passed to the agents directly.
"""

from __future__ import annotations

import anthropic

from hall_monitor import tracing
from hall_monitor.agents.coverage_check import run_coverage_check
from hall_monitor.agents.intake import run_intake
from hall_monitor.agents.recommendation import run_recommendation
from hall_monitor.agents.risk_fraud import run_risk_fraud
from hall_monitor.governance.routing import route_claim
from hall_monitor.models import ClaimSubmission, PolicyRecord, TriageResult
from hall_monitor.registry import Registry


@tracing.observe(name="triage", as_type="chain")
def triage(
    claim: ClaimSubmission,
    *,
    registry: Registry,
    policies: dict[str, PolicyRecord],
    wording: str,
    client: anthropic.Anthropic | None = None,
) -> TriageResult:
    client = client or anthropic.Anthropic()

    intake = run_intake(claim, registry, client)
    extraction = intake.output
    policy = policies.get(extraction.policy_number or "")

    coverage = run_coverage_check(claim, extraction, policy, wording, registry, client)
    risk = run_risk_fraud(claim, extraction, policy, registry, client)
    recommendation = run_recommendation(
        claim.claim_id, extraction, coverage.output, risk.output, registry, client
    )

    return TriageResult(
        claim_id=claim.claim_id,
        extraction=extraction,
        coverage=coverage.output,
        risk=risk.output,
        recommendation=recommendation.output,
        routing=route_claim(coverage.output, risk.output, recommendation.output),
        agent_calls=[intake.call, coverage.call, risk.call, recommendation.call],
    )
