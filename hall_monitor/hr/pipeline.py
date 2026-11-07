"""Prepare a leave request for a person: intake agent, then the rules in code, then an explanation.

    message -> redact identifiers, look for text aimed at the AI
            -> intake agent: dates and categories (no condition)
            -> code: eligibility, entitlement, who decides (hr/eligibility.py, hr/routing.py)
            -> explanation agent: a note for the specialist, advisory only

The route and the role come from code. The intake agent's answer is what the rules run on, so
a wrong extraction changes the result (the eval measures that); the explanation agent can't
change it, and text that addresses the AI sends the request to a person whatever the agents
say. Every outcome is for a
person to sign; nothing here approves or denies leave. Any failure (an agent refusing, a stopped
or unapproved agent, an API error, an unusable answer) stops the request and sends it to a
person, with what was recorded so far.

Each step is written to the audit log as it happens, when one is given: IDs, hashes, counts,
codes and costs, never the message, the extraction's values or the explanation's text. The
evidence pack a person signs against is stored at the end (hr/signoff.py).
"""

from __future__ import annotations

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
from hall_monitor.hr.signoff import record_evidence
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


def _canonical(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


class _Recorder:
    """Writes the request's steps to the audit log as they happen, if there is one.

    The message, the extraction, the code's result and the note are logged as keyed hashes
    (HMAC), never plain ones: an extraction has few possible values (dates, a handful of
    categories), so a plain hash could be confirmed by guessing, revealing the reason category.
    """

    def __init__(self, audit: AuditLog | None, request_id: str):
        self.audit, self.request_id = audit, request_id

    def _hmac(self, value: object) -> str:
        return self.audit.keyed_hash(_canonical(value))

    def submitted(self, message: LeaveMessage, redactions: dict, signals: list[str]) -> None:
        if self.audit:
            self.audit.append(
                self.request_id,
                "leave_request_submitted",
                "employee",
                {
                    "employee_id": message.employee_id,
                    "submitted_at": message.submitted_at.isoformat(),
                    "message_hmac": self._hmac(message.text),
                    "identifiers_redacted": redactions,
                    "injection_signals": signals,
                },
            )

    def agent_call(self, call: AgentCall) -> None:
        if self.audit:
            # The call's plain input hash is keyed before it is logged: the explanation's input
            # has so few possible values that a guess could confirm it.
            payload = call.model_dump(mode="json")
            payload["input_hmac"] = self._hmac(payload.pop("input_sha256"))
            self.audit.append(self.request_id, "agent_call", call.agent_id, payload)

    def assessed(self, intake: LeaveIntake, assessment: Assessment, route: LeaveRoute) -> None:
        if self.audit:
            self.audit.append(
                self.request_id,
                "leave_assessed",
                "pipeline",
                {
                    "intake_hmac": self._hmac(intake.model_dump(mode="json")),
                    "assessment_hmac": self._hmac(assessment.model_dump(mode="json")),
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
                    "explanation_hmac": (
                        None if explanation is None else self._hmac(explanation.model_dump())
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
    ids = [note.id for note in explanation.checks]
    if sorted(ids) != sorted(c.id for c in assessment.checks):
        return "the explanation didn't cover exactly the checks the code made"
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
    if audit is not None and audit.has(message.id, "leave_evidence"):
        # The log, not anything else, says whether a request was prepared: it isn't prepared,
        # and decided, twice.
        raise ValueError(f"the audit log shows {message.id} was already prepared")
    record = _Recorder(audit, message.id)
    counts: Counter[str] = Counter()
    text = redact_text(message.text, counts)
    redactions = {k: v for k, v in sorted(counts.items()) if v}
    # The message as sent and as redacted: redaction can join words a phrase needs.
    signals = sorted(set(detect_in_texts([message.text])) | set(detect_in_texts([text])))
    record.submitted(message, redactions, signals)

    calls: list[AgentCall] = []
    intake = request = assessment = explanation = route = None
    explanation_problem = None
    flags = ["text_addresses_ai"] if signals else []

    def finish(final: LeaveRoute, reason: str | None) -> LeavePreparation:
        nonlocal explanation_problem
        cost, unknown = _cost(calls)
        if unknown is not None:  # a cost that can't be shown is a failure too; keeps the reasons
            final = _with(final, ["pipeline_stopped"])
            reason = unknown if reason is None else f"{reason}; {unknown}"
        if reason is not None and explanation is not None and explanation_problem is None:
            explanation_problem = "the request stopped, so the note was left out"
        prepared = LeavePreparation(
            request_id=message.id,
            employee_id=message.employee_id,
            intake=intake,
            request=request,
            assessment=assessment,
            route=final,
            explanation=explanation if reason is None else None,
            explanation_problem=explanation_problem,
            agent_calls=calls,
            redactions=redactions,
            injection_signals=signals,
            stopped_reason=reason,
            cost_usd=cost,
        )
        record.prepared(prepared)
        if audit is not None:  # what the person signs against (hr/signoff.py)
            record_evidence(audit, prepared)
        return prepared

    def stop(reason: str, reasons: list[str]) -> LeavePreparation:
        """A stop before the code has routed: HR review, with any text-aimed-at-the-AI flag."""
        return finish(_person(reasons + flags), reason)

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
    except Exception as exc:  # anything unforeseen goes to a person; the type only
        keep(getattr(exc, "partial_call", None))
        return stop(f"unexpected {type(exc).__name__}", ["pipeline_stopped"])
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
    try:
        assessment = assess(request, employee, company)
        route = _with(route_leave(assessment, request, employee, company), flags)
    except Exception as exc:  # a date the arithmetic can't handle, say
        assessment = None
        return stop(f"unexpected {type(exc).__name__}", ["pipeline_stopped"])
    record.assessed(intake, assessment, route)

    try:
        explained = run_leave_explainer(message.id, assessment, route, registry, client)
        keep(explained.call)
        explanation = explained.output
        explanation_problem = _explanation_problem(explanation, assessment)
        if explanation_problem:
            explanation = None
    except AgentRefusedError as exc:
        # The note is advisory: without it the request still goes to the same person, and the
        # log records why it is missing.
        keep(exc.call)
        explanation_problem = "the explanation agent gave no usable answer"
    except RegistryError as exc:
        keep(getattr(exc, "partial_call", None))
        explanation_problem = f"hall pass refused: {exc}"
    except (anthropic.APIError, ValidationError) as exc:
        keep(getattr(exc, "partial_call", None))
        explanation_problem = f"{type(exc).__name__} from the model call"
    except Exception as exc:
        keep(getattr(exc, "partial_call", None))
        explanation_problem = f"unexpected {type(exc).__name__}"
    return finish(route, None)
