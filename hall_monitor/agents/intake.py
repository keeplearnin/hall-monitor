"""Intake agent: turn a claim submission into structured fields."""

from __future__ import annotations

import anthropic

from hall_monitor import tracing
from hall_monitor.agents.base import UNTRUSTED_CLAIM_NOTICE, AgentRun, render_claim, run_agent
from hall_monitor.models import ClaimSubmission, IntakeExtraction
from hall_monitor.registry import Registry

AGENT_ID = "intake"
PROMPT_VERSION = "intake-v1"

SYSTEM_PROMPT = f"""\
You are the intake step in a travel insurance claims process. You read one claim submission \
and extract structured fields. You do not judge coverage, fraud or what should happen next.

{UNTRUSTED_CLAIM_NOTICE}

Rules:
- Use only what the submission states. If a field isn't stated, return null.
- claimed_items lists each amount the customer asks for, as they describe it. Include every \
amount they claim, even if two look the same. total_claimed is the total the customer claims.
- incident_date is the date the insured event happened (the illness, injury, delay or loss), \
not the date the claim was filed or the trip was cancelled.
- incident_location is where that event happened, if stated.
- supporting_documents lists the attachment filenames exactly as given."""


# Agent spans, not generations: the Anthropic SDK emits its own generation span with model and
# token usage nested under each agent span, so recording usage here would count it twice.
@tracing.observe(name=AGENT_ID, as_type="agent")
def run_intake(
    claim: ClaimSubmission,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
) -> AgentRun[IntakeExtraction]:
    return run_agent(
        AGENT_ID,
        registry=registry,
        client=client or anthropic.Anthropic(),
        system=SYSTEM_PROMPT,
        content=render_claim(claim),
        output_format=IntakeExtraction,
        prompt_version=PROMPT_VERSION,
        claim_id=claim.claim_id,
    )
