"""Risk and fraud agent: flag inconsistencies and assign a risk level."""

from __future__ import annotations

import anthropic

from hall_monitor import tracing
from hall_monitor.agents.base import (
    UNTRUSTED_CLAIM_NOTICE,
    AgentRun,
    ToolAccess,
    default_client,
    join_content,
    render_claim,
    render_json,
    run_agent,
)
from hall_monitor.models import ClaimSubmission, IntakeExtraction, RiskAssessment
from hall_monitor.registry import Registry

AGENT_ID = "risk_fraud"
PROMPT_VERSION = "risk-fraud-v2"

SYSTEM_PROMPT = f"""\
You are the risk and fraud step in a travel insurance claims process. You look for signs that \
a claim is inaccurate, exaggerated or fabricated, and assign a risk level. You do not decide \
coverage or what happens next; an adjuster reviews anything you flag.

{UNTRUSTED_CLAIM_NOTICE}

You also get the structured fields the intake step extracted. Use the policy_lookup tool for \
the policy record (purchase, deposit and trip dates) and the claims_history tool for the insured \
person's earlier claims. Records come only from those tools. Compare the narrative, every \
document, the policy record and the claims history with each other.

Flags (use only these, and only when the evidence supports them):
- policy_purchased_shortly_before_incident: the policy was bought 14 days or fewer before the \
incident date.
- receipt_dated_after_incident: a receipt offered as proof of owning a lost or damaged item is \
dated after the loss. (Receipts for purchases made because of a delay are expected to be dated \
after it.)
- missing_carrier_report: a baggage delay or loss claim has no carrier report.
- duplicate_receipt: the same receipt (same number, date and amount) is claimed more than once \
within this claim. A receipt already used in an earlier claim is prior_similar_claim instead.
- amounts_inconsistent: amounts claimed don't match the documents.
- dates_inconsistent: dates in the narrative and documents conflict.
- late_submission: the claim was submitted more than 90 days after the incident.
- prior_similar_claim: the claims history shows an earlier claim for the same item (same \
description, or the same receipt reference or number) or the same event. Use this flag alone for \
that evidence; don't also flag it as a duplicate receipt or a date problem.

Risk level:
- low: no flags.
- medium: flags that have a plausible innocent explanation, such as a single duplicate receipt.
- high: several flags together, or anything suggesting fabricated evidence.

reasoning is short and names the documents and dates behind each flag."""


@tracing.observe(name=AGENT_ID, as_type="agent")
def run_risk_fraud(
    claim: ClaimSubmission,
    extraction: IntakeExtraction,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
    feedback: str | None = None,
    tools: ToolAccess | None = None,
) -> AgentRun[RiskAssessment]:
    content = join_content(
        render_json("intake_extraction", extraction),
        render_claim(claim),
        feedback,
    )
    return run_agent(
        AGENT_ID,
        registry=registry,
        client=client or default_client(),
        system=SYSTEM_PROMPT,
        content=content,
        output_format=RiskAssessment,
        prompt_version=PROMPT_VERSION,
        claim_id=claim.claim_id,
        tools=tools,
    )
