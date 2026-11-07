"""Prepare a leave request for a person: intake agent, then the rules in code, then an explanation.

    message -> redact identifiers, look for text aimed at the AI
            -> intake agent: dates and categories (no condition)
            -> code: eligibility, entitlement, who decides (hr/eligibility.py, hr/routing.py)
            -> explanation agent: a note for the specialist, advisory only

The agents can't change the result: the route and the role come from code, and text that
addresses the AI sends the request to a person whatever the agents say. Every outcome is for a
person to sign; nothing here approves or denies leave. Any failure (an agent refusing, a stopped
or unapproved agent, an API error, an unusable answer) stops the request and sends it to a
person, with what was recorded so far.

Each step is written to the audit log as it happens, when one is given: IDs, hashes, counts,
codes and costs, never the message, the extraction's values or the explanation's text.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRefusedError
from hall_monitor.gateway.cost import UnpricedModelError, call_cost
from hall_monitor.gateway.injection import detect_in_texts
from hall_monitor.gateway.redaction import redact_text
from hall_monitor.governance.audit import AuditLog
from hall_monitor.hr.eligibility import Assessment, assess
from hall_monitor.hr.leave_explainer import run_leave_explainer
from hall_monitor.hr.leave_intake import run_leave_intake
from hall_monitor.hr.models import (
    Company,
    Employee,
    LeaveExplanation,
    LeaveIntake,
    LeaveMessage,
    LeaveRequest,
)
from hall_monitor.hr.routing import REASONS, LeaveRoute, Role, route_leave
from hall_monitor.models import AgentCall
from hall_monitor.registry import Registry, RegistryError


class LeavePreparation(BaseModel):
    """What a person gets to decide on. The route and role are the code's."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    employee_id: str
    intake: LeaveIntake | None
    request: LeaveRequest | None
    assessment: Assessment | None
    route: LeaveRoute
    explanation: LeaveExplanation | None
    explanation_problem: str | None
    agent_calls: list[AgentCall]
    redactions: dict[str, int]
    injection_signals: list[str]
    stopped_reason: str | None
    cost_usd: float


def _sha256(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


class _Recorder:
    """Writes the request's steps to the audit log as they happen, if there is one."""

    def __init__(self, audit: AuditLog | None, request_id: str):
        self.audit, self.request_id = audit, request_id

    def submitted(self, message: LeaveMessage, redactions: dict, signals: list[str]) -> None:
        if self.audit:
            self.audit.append(
                self.request_id,
                "leave_request_submitted",
                "employee",
                {
                    "employee_id": message.employee_id,
                    "submitted_at": message.submitted_at.isoformat(),
                    "message_sha256": _sha256(message.text),
                    "identifiers_redacted": redactions,
                    "injection_signals": signals,
                },
            )

    def agent_call(self, call: AgentCall) -> None:
        if self.audit:
            self.audit.append(
                self.request_id, "agent_call", call.agent_id, call.model_dump(mode="json")
            )

    def assessed(self, intake: LeaveIntake, assessment: Assessment, route: LeaveRoute) -> None:
        if self.audit:
            self.audit.append(
                self.request_id,
                "leave_assessed",
                "pipeline",
                {
                    "intake_sha256": _sha256(intake.model_dump(mode="json")),
                    "assessment_sha256": _sha256(assessment.model_dump(mode="json")),
                    "eligible": assessment.eligible,
                    "failed_checks": sorted(c.id for c in assessment.checks if c.passed is False),
                    "unknown_checks": sorted(c.id for c in assessment.checks if c.passed is None),
                    "remaining_weeks": assessment.remaining_weeks,
                    "requested_weeks": assessment.requested_weeks,
                    "route": route.route,
                    "decided_by": route.decided_by,
                    "reasons": route.reasons,
                },
            )

    def prepared(self, prepared: LeavePreparation) -> None:
        if self.audit:
            explanation = prepared.explanation
            self.audit.append(
                self.request_id,
                "leave_prepared",
                "pipeline",
                {
                    "route": prepared.route.route,
                    "decided_by": prepared.route.decided_by,
                    "reasons": prepared.route.reasons,
                    "stopped_reason": prepared.stopped_reason,
                    "explanation_sha256": (
                        None if explanation is None else _sha256(explanation.model_dump())
                    ),
                    "explanation_problem": prepared.explanation_problem,
                    "cost_usd": prepared.cost_usd,
                    "models": sorted({f"{c.agent_id}:{c.model}" for c in prepared.agent_calls}),
                },
            )


def _person(reasons: list[str]) -> LeaveRoute:
    """HR review by a leave specialist or above, for a request the rules couldn't finish."""
    role = max((REASONS[r][1] for r in reasons), default=Role.LEAVE_SPECIALIST)
    return LeaveRoute(
        route="hr_review",
        decided_by=role.label,
        reasons=sorted(reasons),
        explained=[REASONS[r][0] for r in sorted(reasons)],
    )


def _with(route: LeaveRoute, extra: list[str]) -> LeaveRoute:
    """The code's route with more reasons added; the role only goes up."""
    reasons = sorted(set(route.reasons) | set(extra))
    role = max(
        [Role[route.decided_by.upper()], *(REASONS[r][1] for r in extra)],
        default=Role.LEAVE_SPECIALIST,
    )
    return LeaveRoute(
        route="hr_review" if reasons else route.route,
        decided_by=role.label,
        reasons=reasons,
        explained=[REASONS[r][0] for r in reasons],
    )


def _explanation_problem(explanation: LeaveExplanation, assessment: Assessment) -> str | None:
    """The note is only kept if it explains exactly the checks the code made."""
    expected = {c.id for c in assessment.checks}
    if set(explanation.checks) != expected:
        return "the explanation didn't cover exactly the checks the code made"
    if any(len(text) > 400 for text in explanation.checks.values()):
        return "an explanation sentence was too long"
    return None


def _cost(calls: list[AgentCall]) -> tuple[float, str | None]:
    try:
        return round(sum(call_cost(c) for c in calls), 6), None
    except UnpricedModelError as exc:
        return 0.0, f"cost unknown: {exc}"


@tracing.observe(name="leave_request")
def prepare(
    message: LeaveMessage,
    *,
    employees: dict[str, Employee],
    company: Company,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
    audit: AuditLog | None = None,
) -> LeavePreparation:
    record = _Recorder(audit, message.id)
    counts: Counter[str] = Counter()
    text = redact_text(message.text, counts)
    redactions = {k: v for k, v in sorted(counts.items()) if v}
    # The message as sent and as redacted: redaction can join words a phrase needs.
    signals = sorted(set(detect_in_texts([message.text])) | set(detect_in_texts([text])))
    record.submitted(message, redactions, signals)

    calls: list[AgentCall] = []
    intake = request = assessment = explanation = None
    explanation_problem = stopped = None
    flags = ["text_addresses_ai"] if signals else []

    def stop(reason: str, reasons: list[str]) -> LeavePreparation:
        cost, unknown = _cost(calls)
        prepared = LeavePreparation(
            request_id=message.id,
            employee_id=message.employee_id,
            intake=intake,
            request=request,
            assessment=assessment,
            route=_person(reasons + flags),
            explanation=None,
            explanation_problem=None,
            agent_calls=calls,
            redactions=redactions,
            injection_signals=signals,
            stopped_reason=reason if unknown is None else f"{reason}; {unknown}",
            cost_usd=cost,
        )
        record.prepared(prepared)
        return prepared

    def keep(call: AgentCall | None) -> None:
        if call is not None:
            calls.append(call)
            record.agent_call(call)

    try:
        run = run_leave_intake(message, text, registry, client)
    except AgentRefusedError as exc:
        keep(exc.call)
        return stop(f"agent refused or returned no usable output: {exc}", ["pipeline_stopped"])
    except RegistryError as exc:
        keep(getattr(exc, "partial_call", None))
        return stop(f"hall pass refused: {exc}", ["pipeline_stopped"])
    except (anthropic.APIError, ValidationError) as exc:
        keep(getattr(exc, "partial_call", None))
        return stop(f"{type(exc).__name__} from the model call", ["pipeline_stopped"])
    keep(run.call)
    intake = run.output

    employee = employees.get(message.employee_id)
    if employee is None:
        return stop("no employee record for this request", ["facts_missing"])
    if intake.start is None or intake.end is None:
        return stop("the request doesn't give its dates", ["facts_missing"])
    try:
        request = LeaveRequest(
            id=message.id,
            employee_id=message.employee_id,
            notice_date=message.submitted_at.date(),
            **intake.model_dump(),
        )
    except ValidationError as exc:  # an end before the start, say: the type only, no values
        kinds = ", ".join(sorted({e["type"] for e in exc.errors()}))
        return stop(f"the extracted request isn't valid ({kinds})", ["facts_missing"])

    assessment = assess(request, employee, company)
    route = _with(route_leave(assessment, request, employee, company), flags)
    record.assessed(intake, assessment, route)

    try:
        explained = run_leave_explainer(message.id, assessment, route, registry, client)
        keep(explained.call)
        explanation = explained.output
        explanation_problem = _explanation_problem(explanation, assessment)
        if explanation_problem:
            explanation = None
    except AgentRefusedError as exc:
        # The note is advisory: without it the request still goes to the same person.
        keep(exc.call)
        explanation_problem = "the explanation agent gave no usable answer"
    except RegistryError as exc:
        keep(getattr(exc, "partial_call", None))
        explanation_problem = f"hall pass refused: {exc}"
    except (anthropic.APIError, ValidationError) as exc:
        keep(getattr(exc, "partial_call", None))
        explanation_problem = f"{type(exc).__name__} from the model call"

    cost, unknown = _cost(calls)
    prepared = LeavePreparation(
        request_id=message.id,
        employee_id=message.employee_id,
        intake=intake,
        request=request,
        assessment=assessment,
        route=route,
        explanation=explanation,
        explanation_problem=explanation_problem,
        agent_calls=calls,
        redactions=redactions,
        injection_signals=signals,
        stopped_reason=stopped if unknown is None else unknown,
        cost_usd=cost,
    )
    record.prepared(prepared)
    return prepared
