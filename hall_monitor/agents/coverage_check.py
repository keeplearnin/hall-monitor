"""Coverage check agent: compare the claim with the policy wording and decide what it pays."""

from __future__ import annotations

import anthropic

from hall_monitor import tracing
from hall_monitor.agents.base import (
    UNTRUSTED_CLAIM_NOTICE,
    AgentRun,
    ToolAccess,
    join_content,
    render_claim,
    render_json,
    run_agent,
)
from hall_monitor.models import (
    ClaimSubmission,
    CoverageAssessment,
    CoverageFinding,
    IntakeExtraction,
)
from hall_monitor.registry import Registry

AGENT_ID = "coverage_check"
PROMPT_VERSION = "coverage-check-v3"

SYSTEM_PROMPT = """\
You are the coverage check step in a travel insurance claims process. You compare one claim \
with the policy wording below and decide what the policy pays. Assume the claim documents are \
genuine: a separate step checks for fraud, and an adjuster makes the final decision.

{untrusted}

You also get the structured fields the intake step extracted. Check them against the claim \
documents where it matters. Look up the policy record with the policy_lookup tool, using the \
extracted policy number, before you decide: it gives the plan and the purchase, deposit and trip \
dates. Policy records come only from that tool.

Rules:
- Base every conclusion on the policy wording, the policy record and the claim documents.
- Apply the limits for the insured's plan from the schedule of benefits, plus any deductible, \
per-item limit, daily limit and exclusion.
- Where a clause depends on dates (waiting periods, pre-existing conditions, delays), work them \
out from the policy record and the documents.
- covered_amount is what the policy pays in USD. It is 0 if nothing is covered.
- applicable_clauses lists only the clauses that decide the outcome or the amount, by number \
exactly as written in the wording (e.g. "3.1", "5.1").
- reasoning is short and names the clauses and facts you relied on.

<policy_wording>
{wording}
</policy_wording>"""


@tracing.observe(name=AGENT_ID, as_type="agent")
def run_coverage_check(
    claim: ClaimSubmission,
    extraction: IntakeExtraction,
    wording: str,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
    feedback: str | None = None,
    tools: ToolAccess | None = None,
    economy: bool = False,
) -> AgentRun[CoverageAssessment]:
    content = join_content(
        render_json("intake_extraction", extraction),
        render_claim(claim),
        feedback,
    )
    run = run_agent(
        AGENT_ID,
        registry=registry,
        client=client or anthropic.Anthropic(),
        system=SYSTEM_PROMPT.format(untrusted=UNTRUSTED_CLAIM_NOTICE, wording=wording),
        content=content,
        output_format=CoverageFinding,
        prompt_version=PROMPT_VERSION,
        claim_id=claim.claim_id,
        tools=tools,
        economy=economy,
    )
    # The label is defined by the amount, so code sets it rather than the model.
    assessment = CoverageAssessment.from_finding(run.output, extraction.total_claimed)
    return AgentRun(output=assessment, call=run.call)
