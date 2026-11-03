"""Risk and fraud agent: flag inconsistencies and assign a risk level."""

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
from hall_monitor.models import ClaimSubmission, IntakeExtraction, PolicyRecord, RiskAssessment
from hall_monitor.registry import Registry

AGENT_ID = "risk_fraud"
PROMPT_VERSION = "risk-fraud-v1"

SYSTEM_PROMPT = f"""\
You are the risk and fraud step in a travel insurance claims process. You look for signs that \
a claim is inaccurate, exaggerated or fabricated, and assign a risk level. You do not decide \
coverage or what happens next; an adjuster reviews anything you flag.

{UNTRUSTED_CLAIM_NOTICE}

You also get the policy record and the structured fields the intake step extracted. Compare \
the narrative, every document and the policy record with each other.

Flags (use only these, and only when the evidence supports them):
- policy_purchased_shortly_before_incident: the policy was bought 14 days or fewer before the \
incident date.
- receipt_dated_after_incident: a receipt offered as proof of owning a lost or damaged item is \
dated after the loss. (Receipts for purchases made because of a delay are expected to be dated \
after it.)
- missing_carrier_report: a baggage delay or loss claim has no carrier report.
- duplicate_receipt: the same receipt (same number, date and amount) is claimed more than once.
- amounts_inconsistent: amounts claimed don't match the documents.
- dates_inconsistent: dates in the narrative and documents conflict.
- late_submission: the claim was submitted more than 90 days after the incident.

Risk level:
- low: no flags.
- medium: flags that have a plausible innocent explanation, such as a single duplicate receipt.
- high: several flags together, or anything suggesting fabricated evidence.

reasoning is short and names the documents and dates behind each flag."""


@tracing.observe(name=AGENT_ID, as_type="agent")
def run_risk_fraud(
    claim: ClaimSubmission,
    extraction: IntakeExtraction,
    policy: PolicyRecord | None,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
) -> AgentRun[RiskAssessment]:
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
        system=SYSTEM_PROMPT,
        content=content,
        output_format=RiskAssessment,
        prompt_version=PROMPT_VERSION,
        claim_id=claim.claim_id,
    )
