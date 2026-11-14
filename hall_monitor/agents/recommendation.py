"""Recommendation agent: propose a next step with reasons and evidence."""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, ConfigDict

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRun, default_client, render_json, run_agent
from hall_monitor.models import (
    CoverageAssessment,
    IntakeExtraction,
    Recommendation,
    RiskAssessment,
)
from hall_monitor.registry import DataClass, Registry

AGENT_ID = "recommendation"
PROMPT_VERSION = "recommendation-v3"

SYSTEM_PROMPT = """\
You are the recommendation step in a travel insurance claims process. You get a structured \
summary of one claim: its type and total claimed, the coverage decision, amount and clauses, \
and the risk level and flags. You propose a next step. You only propose: an adjuster decides \
every denial, every large payout and every claim with a fraud flag.

You see no claim text, names or medical details, because you don't need them; decide from the \
summary alone.

Next steps:
- deny: the coverage decision is not_covered.
- investigate: the risk level is high.
- request_information: the flags include missing_carrier_report and the risk is not high.
- pay: otherwise, including partial payments of the covered amount.

Apply them in that order. Give each reason with the evidence behind it: the clause or the \
flag."""


class RecommendationInput(BaseModel):
    """Data minimisation: structured fields only. No names, claim text or free-text reasoning.
    The claim type and clause numbers can still show that a claim is medical, so this input is
    declared as health data too."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_type: str
    total_claimed: float
    currency: str
    coverage_decision: str
    covered_amount: float
    applicable_clauses: list[str]
    risk_level: str
    risk_flags: list[str]


def recommendation_input(
    claim_id: str, extraction: IntakeExtraction, coverage: CoverageAssessment, risk: RiskAssessment
) -> RecommendationInput:
    return RecommendationInput(
        claim_id=claim_id,
        claim_type=extraction.claim_type.value,
        total_claimed=extraction.total_claimed,
        currency=extraction.currency,
        coverage_decision=coverage.decision.value,
        covered_amount=coverage.covered_amount,
        applicable_clauses=coverage.applicable_clauses,
        risk_level=risk.level.value,
        risk_flags=[f.value for f in risk.flags],
    )


@tracing.observe(name=AGENT_ID, as_type="agent")
def run_recommendation(
    claim_id: str,
    extraction: IntakeExtraction,
    coverage: CoverageAssessment,
    risk: RiskAssessment,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
    economy: bool = False,
) -> AgentRun[Recommendation]:
    content = render_json(
        "claim_summary", recommendation_input(claim_id, extraction, coverage, risk)
    )
    return run_agent(
        AGENT_ID,
        registry=registry,
        client=client or default_client(),
        system=SYSTEM_PROMPT,
        content=content,
        output_format=Recommendation,
        prompt_version=PROMPT_VERSION,
        claim_id=claim_id,
        economy=economy,
        # Amounts and labels only, but the claim type (emergency_medical) and clause numbers
        # (5.1, pre-existing conditions) can reveal a medical claim: that's health data.
        carries=frozenset({DataClass.FINANCIAL, DataClass.HEALTH}),
    )
