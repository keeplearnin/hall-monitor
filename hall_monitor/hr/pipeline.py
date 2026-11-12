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
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

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
from hall_monitor.hr.supervisor import (
    AGENT_ID as SUPERVISOR,
)
from hall_monitor.hr.supervisor import (
    MAX_INTAKES,
    LeaveAction,
    LeaveDecision,
    LeaveStep,
    LeaveView,
    _Budget,
    _Intake,
    _LastStep,
    _Rules,
    run_leave_supervisor,
)
from hall_monitor.models import AgentCall, StepOutcome, StepRefusal
from hall_monitor.registry import Registry, RegistryError


class LeavePreparation(BaseModel):
    """What a person gets to decide on. The route and role are the code's."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    employee_id: str
    # The message as the intake agent read it, identifiers redacted: for the person who decides,
    # to check the extraction against. It goes into the erasable evidence pack, never the log.
    message_redacted: str
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
    # "agentic": the leave supervisor chose the steps (ADR 0028); "fixed": the old order.
    mode: str = "fixed"
    intake_runs: int = 0
    supervisor_steps: list[LeaveStep] = []
    # The re-check's intake, for the person to compare (the rules ran on intake, the first).
    recheck_intake: LeaveIntake | None = None


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

    def supervisor_step(self, step: LeaveStep) -> None:
        if self.audit:
            # Codes, counts and amounts only (LeaveStep has no free-text field).
            self.audit.append(
                self.request_id, "supervisor_step", SUPERVISOR, step.model_dump(mode="json")
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
                    "mode": prepared.mode,
                    "intake_runs": prepared.intake_runs,
                    "supervisor_path": [s.action.value for s in prepared.supervisor_steps],
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
    agentic: bool = True,
    limits: LeaveLimits | None = None,
    clock: Callable[[], float] | None = None,
) -> LeavePreparation:
    """agentic=True (the default) lets the leave supervisor choose the steps within code's
    bounds (ADR 0028); agentic=False runs the old fixed order: intake, the rules, the note."""
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
    agentic_fields: dict = {}  # the agentic path's mode and path, set before it finishes

    def finish(final: LeaveRoute, reason: str | None, pack: bool = True) -> LeavePreparation:
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
            message_redacted=text,
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
            **agentic_fields,
        )
        record.prepared(prepared)
        if audit is not None and pack:  # what the person signs against (hr/signoff.py)
            record_evidence(audit, prepared)
        return prepared

    def stop(reason: str, reasons: list[str], pack: bool = True) -> LeavePreparation:
        """A stop before the code has routed: HR review, with any text-aimed-at-the-AI flag.
        pack=False for a failure of the intake call itself (an outage, a stopped agent): no
        evidence pack is stored, so the request can be prepared again once it is fixed."""
        return finish(_person(reasons + flags), reason, pack)

    def keep(call: AgentCall | None) -> None:
        if call is not None:
            calls.append(call)
            record.agent_call(call)

    if agentic:
        ctx = _Leave(
            message=message,
            text=text,
            employees=employees,
            company=company,
            registry=registry,
            client=client or anthropic.Anthropic(),
            signals=signals,
            flags=flags,
            calls=calls,
            keep=keep,
            finish=finish,
            stop=stop,
            record=record,
            limits=limits or LeaveLimits(),
            clock=clock or time.monotonic,
        )

        def synced(fn: Callable[..., LeavePreparation]) -> Callable[..., LeavePreparation]:
            """finish and stop read this function's variables: set them from the loop's."""

            def call(*args, **kwargs) -> LeavePreparation:
                try:
                    return sync_and(*args, **kwargs)
                except Exception as exc:
                    # Raised while recording the outcome (another run's pack stands, say): not
                    # a failure of the loop to finish again (review 24).
                    raise _Finishing(exc) from exc

            def sync_and(*args, **kwargs) -> LeavePreparation:
                nonlocal intake, request, assessment, explanation, explanation_problem
                agentic_fields.update(
                    mode="agentic",
                    intake_runs=len(ctx.intakes),
                    supervisor_steps=list(ctx.steps),
                    recheck_intake=ctx.intakes[1] if len(ctx.intakes) > 1 else None,
                )
                intake = ctx.intakes[0] if ctx.intakes else None
                request, assessment = ctx.request, ctx.assessment
                explanation, explanation_problem = ctx.explanation, ctx.explanation_problem
                return fn(*args, **kwargs)

            return call

        ctx.finish, ctx.stop = synced(finish), synced(stop)
        try:
            return _supervise(ctx)
        except _Finishing as finishing:
            raise finishing.error from None

    try:
        run = run_leave_intake(message, text, registry, client)
    except AgentRefusedError as exc:
        keep(exc.call)
        return stop(
            f"agent refused or returned no usable output: {exc}", ["pipeline_stopped"], False
        )
    except RegistryError as exc:
        keep(getattr(exc, "partial_call", None))
        return stop(f"hall pass refused: {exc}", ["pipeline_stopped"], False)
    except (anthropic.APIError, ValidationError) as exc:
        keep(getattr(exc, "partial_call", None))
        return stop(f"{type(exc).__name__} from the model call", ["pipeline_stopped"], False)
    except Exception as exc:  # anything unforeseen goes to a person; the type only
        keep(getattr(exc, "partial_call", None))
        return stop(f"unexpected {type(exc).__name__}", ["pipeline_stopped"], False)
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


# --- The agentic path (ADR 0028) ---------------------------------------------------------------

MAX_REFUSALS = 3


@dataclass(frozen=True)
class LeaveLimits:
    """A request's budget. The old order takes four supervisor steps; a re-check, five."""

    max_steps: int = 8
    max_cost_usd: float = 0.02
    max_seconds: float = 120.0


class _Finishing(BaseException):  # noqa: N818 - not an error of its own; it carries one
    """Carries an error raised while recording the outcome past the loop's own handlers."""

    def __init__(self, error: Exception):
        super().__init__(type(error).__name__)
        self.error = error


class _LeaveStopped(Exception):
    """The leave loop ran out of budget or kept choosing refused steps."""


@dataclass
class _Leave:
    message: LeaveMessage
    text: str
    employees: dict[str, Employee]
    company: Company
    registry: Registry
    client: anthropic.Anthropic
    signals: list[str]
    flags: list[str]
    calls: list[AgentCall]
    keep: Callable[[AgentCall | None], None]
    finish: Callable[..., LeavePreparation]
    stop: Callable[..., LeavePreparation]
    record: _Recorder
    limits: LeaveLimits
    clock: Callable[[], float]
    intakes: list[LeaveIntake] = field(default_factory=list)
    request: LeaveRequest | None = None
    assessment: Assessment | None = None
    route: LeaveRoute | None = None
    explanation: LeaveExplanation | None = None
    explanation_problem: str | None = None
    explainer_ran: bool = False
    steps: list[LeaveStep] = field(default_factory=list)
    started: float = 0.0


def _leave_refusal(ctx: _Leave, decision: LeaveDecision) -> StepRefusal | None:
    A = LeaveAction
    match decision.action:
        case A.RUN_INTAKE:
            return StepRefusal.REPEAT_WITHOUT_CHANGE if ctx.intakes else None
        case A.RECHECK_INTAKE:
            if not ctx.intakes:
                return StepRefusal.PRECONDITION_UNMET
            if len(ctx.intakes) >= MAX_INTAKES or ctx.assessment is not None:
                # A re-check after the rules ran could only be shopping for another answer.
                return StepRefusal.REPEAT_WITHOUT_CHANGE
            return None
        case A.ASSESS:
            if not ctx.intakes:
                return StepRefusal.PRECONDITION_UNMET
            return StepRefusal.REPEAT_WITHOUT_CHANGE if ctx.assessment is not None else None
        case A.RUN_EXPLAINER:
            if ctx.assessment is None:
                return StepRefusal.PRECONDITION_UNMET
            return StepRefusal.REPEAT_WITHOUT_CHANGE if ctx.explainer_ran else None
        case _:
            return None


def _leave_view(ctx: _Leave) -> LeaveView:
    last = ctx.steps[-1] if ctx.steps else None
    cost = _cost(ctx.calls)[0]
    return LeaveView(
        injection_signals=len(ctx.signals),
        intakes=[
            _Intake(
                reason=i.reason,
                family_relationship=i.family_relationship,
                intermittent=i.intermittent,
                has_dates=i.start is not None and i.end is not None,
            )
            for i in ctx.intakes
        ],
        max_intakes=MAX_INTAKES,
        rules=None
        if ctx.route is None
        else _Rules(
            route=ctx.route.route, decided_by=ctx.route.decided_by, reasons=ctx.route.reasons
        ),
        explainer_ran=ctx.explainer_ran,
        explained=ctx.explanation is not None,
        last_step=None
        if last is None
        else _LastStep(action=last.action, outcome=last.outcome, refusal=last.refusal),
        budget_left=_Budget(
            steps=max(ctx.limits.max_steps - len(ctx.steps), 0),
            cost_usd=round(max(ctx.limits.max_cost_usd - cost, 0.0), 6),
            seconds=round(max(ctx.limits.max_seconds - (ctx.clock() - ctx.started), 0.0), 1),
        ),
    )


def _spend(ctx: _Leave) -> None:
    cost, unknown = _cost(ctx.calls)
    if unknown is not None:  # an unpriced model would read as free
        raise _LeaveStopped(f"budget can't be checked: {unknown}")
    if cost >= ctx.limits.max_cost_usd:
        raise _LeaveStopped(f"budget exhausted: cost reached {ctx.limits.max_cost_usd} USD")
    if ctx.clock() - ctx.started >= ctx.limits.max_seconds:
        raise _LeaveStopped(f"budget exhausted: {ctx.limits.max_seconds:g} seconds")


def _step(ctx: _Leave, decision: LeaveDecision, outcome, refusal=None, agents=()) -> None:
    step = LeaveStep(
        step=len(ctx.steps) + 1,
        action=decision.action,
        reason=decision.reason,
        outcome=outcome,
        refusal=refusal,
        agents=list(agents),
        cost_usd=_cost(ctx.calls)[0],
        seconds=round(ctx.clock() - ctx.started, 2),
    )
    ctx.steps.append(step)
    ctx.record.supervisor_step(step)


def _done(ctx: _Leave, result: LeavePreparation) -> LeavePreparation:
    return result.model_copy(
        update={
            "mode": "agentic",
            "intake_runs": len(ctx.intakes),
            "supervisor_steps": list(ctx.steps),
        }
    )


def _assess(ctx: _Leave) -> tuple[str, list[str]] | None:
    """The rules on the first intake, with the fixed path's code checks. Returns the stop's
    reason and codes when a check fails, else None."""
    intake = ctx.intakes[0]
    disagreed = len(ctx.intakes) > 1 and len({i.model_dump_json() for i in ctx.intakes}) > 1
    flag = ["intake_disagreed"] if disagreed else []  # kept on every path (review 24)
    employee = ctx.employees.get(ctx.message.employee_id)
    if employee is None:
        return ("no employee record for this request", ["facts_missing", *flag])
    if intake.start is None or intake.end is None:
        return ("the request doesn't give its dates", ["facts_missing", *flag])
    try:
        ctx.request = LeaveRequest(
            id=ctx.message.id,
            employee_id=ctx.message.employee_id,
            notice_date=ctx.message.submitted_at.date(),
            **intake.model_dump(),
        )
    except ValidationError as exc:  # the type only, no values
        kinds = ", ".join(sorted({e["type"] for e in exc.errors()}))
        return (f"the extracted request isn't valid ({kinds})", ["facts_missing", *flag])
    try:
        ctx.assessment = assess(ctx.request, employee, ctx.company)
        extra = [*ctx.flags, *flag]
        ctx.route = _with(route_leave(ctx.assessment, ctx.request, employee, ctx.company), extra)
    except Exception as exc:
        ctx.assessment = None
        return (f"unexpected {type(exc).__name__}", ["pipeline_stopped", *flag])
    ctx.record.assessed(intake, ctx.assessment, ctx.route)
    return None


def _explain(ctx: _Leave) -> None:
    """The note is advisory, as on the fixed path: a failure is recorded, never a stop."""
    ctx.explainer_ran = True
    try:
        explained = run_leave_explainer(
            ctx.message.id, ctx.assessment, ctx.route, ctx.registry, ctx.client
        )
        ctx.keep(explained.call)
        ctx.explanation = explained.output
        ctx.explanation_problem = _explanation_problem(ctx.explanation, ctx.assessment)
        if ctx.explanation_problem:
            ctx.explanation = None
    except AgentRefusedError as exc:
        ctx.keep(exc.call)
        ctx.explanation_problem = "the explanation agent gave no usable answer"
    except RegistryError as exc:
        ctx.keep(getattr(exc, "partial_call", None))
        ctx.explanation_problem = f"hall pass refused: {exc}"
    except (anthropic.APIError, ValidationError) as exc:
        ctx.keep(getattr(exc, "partial_call", None))
        ctx.explanation_problem = f"{type(exc).__name__} from the model call"
    except Exception as exc:
        ctx.keep(getattr(exc, "partial_call", None))
        ctx.explanation_problem = f"unexpected {type(exc).__name__}"


def _end(ctx: _Leave, decision: LeaveDecision) -> LeavePreparation:
    """Finish or hand off. Code routes: without the rules, HR review (assessment_skipped);
    a hand-off adds supervisor_handed_off. Every outcome is for a person to sign."""
    for agent_id in dict.fromkeys(c.agent_id for c in ctx.calls):
        if agent_id == "leave_explainer":  # advisory: a stop drops the note, not the request
            try:
                ctx.registry.require(agent_id)
            except RegistryError:
                ctx.explanation = None
                ctx.explanation_problem = "the explanation agent was stopped after it answered"
            continue
        ctx.registry.require(agent_id)  # a stop since an agent answered: not on its answer
    handed = decision.action is LeaveAction.HAND_OFF
    _step(ctx, decision, StepOutcome.HANDED_OFF if handed else StepOutcome.FINISHED)
    extra = ["supervisor_handed_off"] if handed else []
    if ctx.route is None:
        # A choice, not an outage: the pack is stored so HR can sign it (review 24).
        return ctx.stop(
            "the leave supervisor ended before the rules ran", [*extra, "assessment_skipped"]
        )
    route = _with(ctx.route, extra) if extra else ctx.route
    if ctx.explanation is None and ctx.explanation_problem is None:
        ctx.explanation_problem = "the leave supervisor finished without the note"
    return ctx.finish(route, None)


def _supervise(ctx: _Leave) -> LeavePreparation:
    """The leave request's agentic loop. Before each supervisor turn code checks the step
    budget, and before each model call cost and time; before the chosen step runs, the
    supervisor's hall pass; before routing, every agent's. A failure of the loop (budget,
    refusals, the supervisor's own call or hall pass) stops the request for HR review as
    supervisor_failed; an intake failure is pipeline_stopped, as on the fixed path. Neither
    stores an evidence pack unless an intake answered, so the request can be prepared again."""
    ctx.started = ctx.clock()
    refusals = 0
    stage = "supervisor"
    try:
        while True:
            stage = "supervisor"
            if len(ctx.steps) >= ctx.limits.max_steps:
                raise _LeaveStopped(f"budget exhausted: {ctx.limits.max_steps} supervisor steps")
            _spend(ctx)
            run = run_leave_supervisor(ctx.message.id, _leave_view(ctx), ctx.registry, ctx.client)
            ctx.keep(run.call)
            decision = run.output
            if decision.action in (LeaveAction.FINISH, LeaveAction.HAND_OFF):
                try:
                    return _done(ctx, _end(ctx, decision))
                except RegistryError:
                    if not ctx.steps or ctx.steps[-1].action is not decision.action:
                        _step(ctx, decision, StepOutcome.FAILED)
                    raise
            refusal = _leave_refusal(ctx, decision)
            if refusal is not None:
                refusals += 1
                _step(ctx, decision, StepOutcome.REFUSED, refusal)
                if refusals >= MAX_REFUSALS:
                    raise _LeaveStopped(f"supervisor loop: {MAX_REFUSALS} steps refused")
                continue
            try:
                ctx.registry.require(SUPERVISOR)  # a stop since it chose ends the step here
                if decision.action in (LeaveAction.RUN_INTAKE, LeaveAction.RECHECK_INTAKE):
                    _spend(ctx)
                    stage = "intake"
                    intake_run = run_leave_intake(ctx.message, ctx.text, ctx.registry, ctx.client)
                    ctx.keep(intake_run.call)
                    ctx.intakes.append(intake_run.output)
                    agents = ["leave_intake"]
                elif decision.action is LeaveAction.ASSESS:
                    agents = []
                    stopped = _assess(ctx)
                    if stopped is not None:  # a code check failed: a person, as before
                        _step(ctx, decision, StepOutcome.RAN)
                        return _done(ctx, ctx.stop(*stopped))
                else:
                    _spend(ctx)
                    _explain(ctx)
                    agents = ["leave_explainer"]
            except Exception:
                _step(
                    ctx, decision, StepOutcome.FAILED, agents=[stage] if stage == "intake" else []
                )
                raise
            _step(ctx, decision, StepOutcome.RAN, agents=agents)
    except _LeaveStopped as exc:
        return _done(ctx, _failed(ctx, f"leave supervisor: {exc}", "supervisor", outage=False))
    except AgentRefusedError as exc:
        ctx.keep(exc.call)
        return _done(ctx, _failed(ctx, f"agent refused or returned no usable output: {exc}", stage))
    except RegistryError as exc:
        ctx.keep(getattr(exc, "partial_call", None))
        return _done(ctx, _failed(ctx, f"hall pass refused: {exc}", stage))
    except (anthropic.APIError, ValidationError) as exc:
        ctx.keep(getattr(exc, "partial_call", None))
        return _done(ctx, _failed(ctx, f"{type(exc).__name__} from the model call", stage))
    except Exception as exc:  # anything unforeseen goes to a person; the type only
        ctx.keep(getattr(exc, "partial_call", None))
        return _done(ctx, _failed(ctx, f"unexpected {type(exc).__name__}", stage))


def _failed(ctx: _Leave, reason: str, stage: str, outage: bool = True) -> LeavePreparation:
    """Only a failure of the intake call itself, before any intake answered, leaves the request
    to be prepared again with no pack, as on the fixed path. Any failure of the supervisor or
    its loop stores the pack, so a broken supervisor never keeps a request from HR (review 24
    and the offline eval)."""
    outage = outage and stage == "intake"
    code = "pipeline_stopped" if stage == "intake" else "supervisor_failed"
    if ctx.route is not None:  # the rules ran: keep their reasons, the role only goes up
        return ctx.finish(_with(ctx.route, [code]), reason)
    # The rules never ran: the most senior role decides (assessment_skipped).
    return ctx.stop(reason, [code, "assessment_skipped"], bool(ctx.intakes) or not outage)
