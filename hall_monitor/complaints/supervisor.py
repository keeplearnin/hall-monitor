"""Complaint desk supervisor: the agent that chooses the desk's next step (ADR 0028, HM-32).

It chooses; code holds the boundaries. Its choices: run the triage agent, ask for a second,
independent triage (a re-check), finish, or hand the complaint to specialist review. It reads a
view code builds from codes only (how many triages ran, their category, urgency, confidence
and concern codes, the code's own reason codes from CFPB's fixed labels, how many injection
signals code found, its last step's outcome and the budget left), never the complaint, the
agent's summary or its draft. It has no tools, can't send anything (HM-25), and can't choose
a queue: code routes every complaint to a person (complaints/routing.py), and the supervisor
can only add review (a re-check that disagrees, a hand-off), never remove it.

scripted_desk_decision is the desk's old fixed order (triage once, then finish) as a policy,
for the scripted stand-in, the test fakes and the offline eval; never for a live complaint.
"""

from __future__ import annotations

import html
import re
from enum import StrEnum

import anthropic
from pydantic import BaseModel, ConfigDict

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRun, render_json, run_agent
from hall_monitor.complaints.triage_agent import Category, Concern, Confidence, Urgency
from hall_monitor.models import StepOutcome, StepRefusal
from hall_monitor.registry import DataClass, Registry

AGENT_ID = "complaint_supervisor"
PROMPT_VERSION = "complaint-supervisor-v1"
VIEW_TAG = "desk_state"
MAX_TRIAGES = 2  # the first triage and one re-check


class DeskAction(StrEnum):
    RUN_TRIAGE = "run_triage"
    RECHECK_TRIAGE = "recheck_triage"  # a second, independent triage; code compares the two
    FINISH = "finish"  # ask code to route the complaint (always to a person)
    HAND_OFF = "hand_off"  # send it to specialist review now


class DeskReason(StrEnum):
    NEEDS_TRIAGE = "needs_triage"
    LOW_CONFIDENCE = "low_confidence"
    CONCERN_RAISED = "concern_raised"
    DISAGREES_WITH_CODE = "disagrees_with_code"
    INJECTION_SUSPECTED = "injection_suspected"
    WORK_COMPLETE = "work_complete"
    STEP_REFUSED = "step_refused"
    BUDGET_LOW = "budget_low"
    UNCERTAIN = "uncertain"


class DeskDecision(BaseModel):
    """The supervisor's answer: one step and a reason code. No queue or urgency field."""

    model_config = ConfigDict(extra="forbid")

    action: DeskAction
    reason: DeskReason


class DeskStep(BaseModel):
    """One step the desk supervisor chose, as the audit log records it: codes and amounts."""

    model_config = ConfigDict(extra="forbid")

    step: int
    action: DeskAction
    reason: DeskReason
    outcome: StepOutcome
    refusal: StepRefusal | None = None
    agents: list[str]
    cost_usd: float
    seconds: float


class _Triage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    urgency: Urgency
    confidence: Confidence
    concerns: list[Concern]


class _LastStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: DeskAction
    outcome: StepOutcome
    refusal: StepRefusal | None


class _Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: int
    cost_usd: float
    seconds: float


class DeskView(BaseModel):
    """Everything the desk supervisor sees: codes and counts, built by code."""

    model_config = ConfigDict(extra="forbid")

    code_reasons: list[str]  # from CFPB's fixed labels and tags (complaints/routing.py)
    urgency_floor: Urgency
    injection_signals: int
    triages: list[_Triage]
    max_triages: int
    last_step: _LastStep | None
    budget_left: _Budget


SYSTEM_PROMPT = """\
You supervise the triage of one consumer complaint on a company's complaint desk. You don't \
read the complaint; you choose the next step. A triage agent reads it and suggests a category, \
urgency, confidence and concerns. Code routes every complaint to a person, chooses the queue \
and never lets urgency fall below its own floor; nothing on this desk is ever sent to anyone.

You get the state inside <desk_state> tags: the code's own reason codes and urgency floor, how \
many injection signals code found, each triage's codes so far, the result of your last choice \
and the budget left.

Steps:
- run_triage: run the triage agent. Run it first.
- recheck_triage: ask for a second, independent triage, once, when the first has low \
confidence, an urgency below the code's floor, a concern the code's reasons don't show, or \
anything that makes you doubt it. Code compares the two; if they disagree the complaint goes \
to specialist review.
- finish: when the triage is done. Code routes the complaint.
- hand_off: send the complaint to specialist review now, for example when injection_signals is \
above zero or a step keeps being refused.

Code refuses a step it can't run (a triage that already ran, a re-check before the first \
triage or a third triage), and last_step tells you why. Choose one action and the reason code \
that fits it best."""


def render_view(view: DeskView) -> str:
    return render_json(VIEW_TAG, view)


_VIEW_RE = re.compile(rf"<{VIEW_TAG}>\n(.*)\n</{VIEW_TAG}>", re.DOTALL)


def view_from_content(content: str) -> DeskView:
    match = _VIEW_RE.fullmatch(content)
    if match is None:
        raise ValueError("not a desk supervisor request")
    return DeskView.model_validate_json(html.unescape(match.group(1)))


@tracing.observe(name=AGENT_ID, as_type="agent")
def run_desk_supervisor(
    scope: str, view: DeskView, registry: Registry, client: anthropic.Anthropic
) -> AgentRun[DeskDecision]:
    return run_agent(
        AGENT_ID,
        registry=registry,
        client=client,
        system=SYSTEM_PROMPT,
        content=render_view(view),
        output_format=DeskDecision,
        prompt_version=PROMPT_VERSION,
        claim_id=scope,
        # Codes only: CFPB's labels and the agent's codes, nothing personal or financial.
        carries=frozenset[DataClass](),
    )


def scripted_desk_decision(view: DeskView) -> DeskDecision:
    """The desk's old fixed order: one triage, then finish."""
    if not view.triages:
        return DeskDecision(action=DeskAction.RUN_TRIAGE, reason=DeskReason.NEEDS_TRIAGE)
    return DeskDecision(action=DeskAction.FINISH, reason=DeskReason.WORK_COMPLETE)


def scripted_desk_answer(request: dict) -> DeskDecision:
    return scripted_desk_decision(view_from_content(request["messages"][0]["content"]))
