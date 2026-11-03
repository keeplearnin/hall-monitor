"""Coverage check agent: compare the claim with the policy wording and decide what it pays."""

from __future__ import annotations

import anthropic

from hall_monitor import tracing
from hall_monitor.agents.base import (
    UNTRUSTED_CLAIM_NOTICE,
    AgentRun,
    render_claim,
    render_json,
    render_policy_record,
    run_agent,
)
from hall_monitor.models import ClaimSubmission, CoverageAssessment, IntakeExtraction, PolicyRecord
from hall_monitor.registry import Registry

AGENT_ID = "coverage_check"
PROMPT_VERSION = "coverage-check-v1"

SYSTEM_PROMPT = """\
You are the coverage check step in a travel insurance claims process. You compare one claim \
with the policy wording below and decide what the policy pays. Assume the claim documents are \
genuine: a separate step checks for fraud, and an adjuster makes the final decision.

{untrusted}

You also get the policy record (plan, purchase and trip dates) and the structured fields the \
intake step extracted. Check the extracted fields against the claim documents where it matters.

Rules:
- Base every conclusion on the policy wording, the policy record and the claim documents.
- Apply the limits for the insured's plan from the schedule of benefits, plus any deductible, \
per-item limit, daily limit and exclusion.
- Where a clause depends on dates (waiting periods, pre-existing conditions, delays), work them \
out from the policy record and the documents.
- covered_amount is what the policy pays in USD. It is 0 if nothing is covered.
- decision is "covered" if covered_amount equals the total claimed, "partially_covered" if it \
is more than 0 but less, and "not_covered" if it is 0.
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
    policy: PolicyRecord | None,
    wording: str,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
) -> AgentRun[CoverageAssessment]:
    content = "\n\n".join(
        [
            render_policy_record(policy),
            render_json("intake_extraction", extraction),
            render_claim(claim),
        ]
    )
    return run_agent(
        AGENT_ID,
        registry=registry,
        client=client or anthropic.Anthropic(),
        system=SYSTEM_PROMPT.format(untrusted=UNTRUSTED_CLAIM_NOTICE, wording=wording),
        content=content,
        output_format=CoverageAssessment,
        prompt_version=PROMPT_VERSION,
        claim_id=claim.claim_id,
    )
