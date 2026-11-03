"""Critic agent: check the other agents' work against the source documents."""

from __future__ import annotations

import anthropic

from hall_monitor import tracing
from hall_monitor.agents.base import (
    UNTRUSTED_CLAIM_NOTICE,
    AgentRun,
    join_content,
    render_claim,
    render_json,
    render_policy_record,
    run_agent,
)
from hall_monitor.models import (
    ClaimSubmission,
    CoverageAssessment,
    CriticReview,
    IntakeExtraction,
    PolicyRecord,
    RiskAssessment,
)
from hall_monitor.registry import Registry

AGENT_ID = "critic"
PROMPT_VERSION = "critic-v1"

SYSTEM_PROMPT = """\
You are the critic in a travel insurance claims process. Three steps have already worked on \
one claim: intake extracted its fields, coverage check decided what the policy pays, and risk \
and fraud flagged concerns. You check their work against the claim documents, the policy \
record and the policy wording below, and report problems so the step can correct them.

{untrusted}

Check, in this order:
1. Intake: every extracted field matches what the claim states. A field that the claim states \
but intake left null is a problem.
2. Coverage check: the covered amount follows from the reasoning. Recalculate it yourself: \
apply the plan's limits, per-item and valuables limits, daily delay limits, deductible and \
depreciation from the policy wording. If the reasoning names a cap or deduction that the \
amount doesn't apply, that is a problem. Also check that the clauses cited are the ones the \
reasoning relies on.
3. Risk and fraud: each flag is supported by evidence in the documents and matches its \
definition. One underlying problem should produce one flag; a second flag for the same \
evidence is a problem. A missing flag that the evidence clearly supports is a problem.

Rules:
- Report only problems you can point to in the documents, the policy record or the wording, \
and give that evidence. Don't report differences of style or wording.
- Don't second-guess a judgement the policy leaves open. Report errors, not preferences.
- If everything checks out, return an empty list of issues.

<policy_wording>
{wording}
</policy_wording>"""


@tracing.observe(name=AGENT_ID, as_type="agent")
def run_critic(
    claim: ClaimSubmission,
    extraction: IntakeExtraction,
    policy: PolicyRecord | None,
    coverage: CoverageAssessment,
    risk: RiskAssessment,
    wording: str,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
) -> AgentRun[CriticReview]:
    content = join_content(
        render_policy_record(policy),
        render_json("intake_extraction", extraction),
        render_json("coverage_assessment", coverage),
        render_json("risk_assessment", risk),
        render_claim(claim),
    )
    return run_agent(
        AGENT_ID,
        registry=registry,
        client=client or anthropic.Anthropic(),
        system=SYSTEM_PROMPT.format(untrusted=UNTRUSTED_CLAIM_NOTICE, wording=wording),
        content=content,
        output_format=CriticReview,
        prompt_version=PROMPT_VERSION,
        claim_id=claim.claim_id,
    )
