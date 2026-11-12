"""Leave explanation agent: explain the code's result to the leave specialist.

It reads the checks and the route the code produced, never the employee's message, and writes
advisory text. The result it explains is the code's and stays the code's.
"""

from __future__ import annotations

import json

import anthropic

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRun, default_client, escape, run_agent
from hall_monitor.hr.eligibility import Assessment
from hall_monitor.hr.models import LeaveExplanation
from hall_monitor.hr.routing import LeaveRoute
from hall_monitor.registry import DataClass, Registry

AGENT_ID = "leave_explainer"
PROMPT_VERSION = "leave-explainer-v1"
# The reason category (an own serious health condition, say) is health information.
CARRIES = frozenset({DataClass.HEALTH})

SYSTEM_PROMPT = """\
You explain a leave-of-absence result to an HR leave specialist. Code checked the federal FMLA \
rules and decided the route; you cannot change either, and the specialist decides the request.

The result is inside <leave_result> tags: each check with its rule, its answer (passed true, \
false, or null when the records can't decide it) and the facts it used, then the route and \
its reasons.

Rules:
- For each check id given, write one plain sentence saying what the rule asks and why the \
answer is what it is, using only the facts given. Use exactly the check ids given.
- Never contradict the result: don't call a failed check met, a check that can't be decided \
met or failed, or the employee eligible when the result says otherwise.
- Don't guess facts that aren't given, and don't mention or guess any medical condition.
- The summary says, in two or three sentences, what the specialist should look at first."""


def render_result(assessment: Assessment, route: LeaveRoute) -> str:
    """The code's result, without the employee or request IDs: the agent doesn't need them."""
    result = {
        "eligible": assessment.eligible,
        "checks": [
            {"id": c.id, "passed": c.passed, "rule": c.rule, "facts": c.facts}
            for c in assessment.checks
        ],
        "remaining_weeks": assessment.remaining_weeks,
        "requested_weeks": assessment.requested_weeks,
        "route": route.route,
        "decided_by": route.decided_by,
        "reasons": route.explained,
    }
    return f"<leave_result>\n{escape(json.dumps(result, indent=2, default=str))}\n</leave_result>"


@tracing.observe(name=AGENT_ID, as_type="agent")
def run_leave_explainer(
    request_id: str,
    assessment: Assessment,
    route: LeaveRoute,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
) -> AgentRun[LeaveExplanation]:
    return run_agent(
        AGENT_ID,
        registry=registry,
        client=client or default_client(),
        system=SYSTEM_PROMPT,
        content=render_result(assessment, route),
        output_format=LeaveExplanation,
        prompt_version=PROMPT_VERSION,
        claim_id=request_id,
        carries=CARRIES,
    )
