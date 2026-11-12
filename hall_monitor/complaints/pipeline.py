"""Prepare one complaint for a person.

    complaint -> redact the free text, look for text aimed at the AI     (code)
              -> complaint triage agent: summary, category, urgency,     (model, advisory)
                 concerns, a draft acknowledgement
              -> routing: which queue, why, how urgent                    (code)
              -> the draft, checked and kept, never sent                  (code, HM-25)
              -> audit log: IDs, hashes, codes, cost                      (code)

Every complaint ends with a person; the route says which queue and why. Any failure of the agent
(a refusal, a stop, an unapproved prompt, an API error, an answer that doesn't validate) sends
the complaint to specialist review with the reason agent_failed, and never invents an answer.
A draft that promises an outcome or carries a link, email address or phone number is not kept;
the log records why.

Audit entries are scoped "cfpb-<complaint id>", so they can't collide with claims or leave
requests. They carry IDs, hashes, codes and costs; never the summary or the draft's text.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRefusedError
from hall_monitor.complaints.drafts import DraftResponse, store_draft
from hall_monitor.complaints.models import Complaint
from hall_monitor.complaints.routing import (
    REASONS,
    ComplaintRoute,
    route_complaint,
    urgency_floor,
)
from hall_monitor.complaints.routing import code_reasons as _code_reasons
from hall_monitor.complaints.supervisor import (
    AGENT_ID as SUPERVISOR,
)
from hall_monitor.complaints.supervisor import (
    MAX_TRIAGES,
    DeskAction,
    DeskDecision,
    DeskStep,
    DeskView,
    _Budget,
    _LastStep,
    _Triage,
    run_desk_supervisor,
)
from hall_monitor.complaints.triage_agent import AGENT_ID as TRIAGE_AGENT
from hall_monitor.complaints.triage_agent import (
    PROMPT_VERSION,
    ComplaintTriage,
    Urgency,
    render_complaint,
    run_complaint_triage,
)
from hall_monitor.gateway.cost import UnpricedModelError, call_cost
from hall_monitor.gateway.injection import detect_in_texts
from hall_monitor.gateway.redaction import redact_text
from hall_monitor.governance.audit import AlreadyRecorded, AuditLog
from hall_monitor.models import AgentCall, StepOutcome, StepRefusal
from hall_monitor.registry import Registry, RegistryError

ROUTED = "complaint_routed"
RECEIVED = "complaint_received"

# A draft is for a person to adapt, not a commitment: one that promises money or an outcome, or
# carries a way to reach someone, is not kept.
_DRAFT_PROBLEMS: dict[str, re.Pattern[str]] = {
    "promises_an_outcome": re.compile(
        r"\b(?:refund|reimburs|compensat|guarantee"
        r"|(?:will|'ll) (?:\w+ )?(?:refund|credit|waive|remove|delete|reverse|correct|fix|resolve|"
        r"pay|close|cancel|forgive|update your|adjust)"
        r"|will be (?:credited|waived|removed|refunded|reversed|corrected|deleted|closed|paid|"
        r"fixed|resolved|cancelled|canceled|forgiven|adjusted)"
        r"|has been (?:resolved|refunded|credited|waived|corrected|fixed)"
        r"|within \d+ (?:business |working )?(?:days?|hours?|weeks?))",
        re.I,
    ),
    "has_a_link": re.compile(
        r"https?://|www\.|\b[a-z0-9-]+\.(?:com|org|net|gov|io|co|us|uk|info|biz)\b", re.I
    ),
    "has_an_email_address": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    # Digits in groups, as phone numbers are written; one unbroken run (a reference) isn't one.
    "has_a_phone_number": re.compile(
        r"(?:\+\d{1,3}[\s.-]?)?\(?\b\d{2,4}\)?(?:[\s.-]\d{3,4}){2}\b|\b\d{3}[\s.-]\d{4}\b"
    ),
}


class AlreadyTriaged(ValueError):
    pass


def is_scripted(client: object) -> bool:
    """True for the scripted stand-in, which marks itself; anything else is taken as a model."""
    return getattr(client, "hall_monitor_scripted", False) is True


class ComplaintResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complaint_id: str
    scope_id: str
    triage: ComplaintTriage | None
    route: ComplaintRoute
    draft_sha256: str | None
    draft_problems: list[str]
    injection_signals: list[str]
    redactions: dict[str, int]
    agent_calls: list[AgentCall]
    stopped_reason: str | None
    cost_usd: float
    # "agentic": the desk supervisor chose the steps (ADR 0028); "fixed": one triage, in code.
    mode: str = "fixed"
    triage_runs: int = 0
    supervisor_steps: list[DeskStep] = []


MAX_REFUSALS = 3


@dataclass(frozen=True)
class DeskLimits:
    """A complaint's budget. The scripted order takes two supervisor steps; a re-check, three."""

    max_steps: int = 6
    max_cost_usd: float = 0.02
    max_seconds: float = 120.0


class DeskStopped(Exception):
    """The desk loop ran out of budget or kept choosing refused steps."""


def scope_id(complaint_id: str) -> str:
    return f"cfpb-{complaint_id}"


def draft_problems(text: str) -> list[str]:
    return sorted(name for name, pattern in _DRAFT_PROBLEMS.items() if pattern.search(text))


def _cost(calls: list[AgentCall]) -> tuple[float, str | None]:
    try:
        return round(sum(call_cost(c) for c in calls), 6), None
    except UnpricedModelError as exc:
        return 0.0, f"cost unknown: {exc}"


@tracing.observe(name="complaint_triage_pipeline")
def triage_complaint(
    complaint: Complaint,
    *,
    registry: Registry,
    client: anthropic.Anthropic,
    audit: AuditLog | None = None,
    agentic: bool = True,
    limits: DeskLimits | None = None,
    clock: Callable[[], float] | None = None,
) -> ComplaintResult:
    """agentic=True (the default) lets the desk supervisor choose the steps within code's
    bounds (ADR 0028); agentic=False runs the old fixed order, one triage.

    The routed entry says whether the answer came from the scripted stand-in, read from the
    client itself (complaints/scripted.py), so a log never shows its answers as the model's."""
    scope = scope_id(complaint.complaint_id)
    if audit is not None and audit.has(scope, RECEIVED):
        raise AlreadyTriaged(f"the audit log shows {scope} was already triaged")
    counts: Counter[str] = Counter()
    narrative = redact_text(complaint.narrative, counts) if complaint.narrative else None
    public = (
        redact_text(complaint.company_public_response, counts)
        if complaint.company_public_response
        else None
    )
    redactions = {k: v for k, v in sorted(counts.items()) if v}
    # Every field the agent is sent is scanned, fixed-list labels included.
    labels = [
        complaint.product,
        complaint.sub_product,
        complaint.issue,
        complaint.sub_issue,
        complaint.submitted_via,
    ]
    raw = [t for t in [*labels, complaint.narrative, complaint.company_public_response] if t]
    redacted = [t for t in [narrative, public] if t]
    signals = sorted(set(detect_in_texts(raw)) | set(detect_in_texts(redacted)))
    if audit is not None:
        # Written once, inside the log's write lock, before the model is called: a second worker
        # on the same complaint stops here, with no model call and nothing else written.
        try:
            audit.append(
                scope,
                "complaint_received",
                "pipeline",
                {
                    "complaint_id": complaint.complaint_id,
                    "content_sha256": complaint.content_sha256(),
                    "identifiers_redacted": redactions,
                    "injection_signals": signals,
                },
                once=True,
            )
        except AlreadyRecorded:
            raise AlreadyTriaged(f"the audit log shows {scope} was already triaged") from None

    try:
        return _prepare(
            complaint,
            scope,
            narrative,
            public,
            signals,
            redactions,
            registry,
            client,
            audit,
            agentic,
            limits or DeskLimits(),
            clock or time.monotonic,
        )
    except BaseException:
        # A crash or interruption after the complaint was claimed: route it to a person now if
        # the log can still be written, so it can't drop out of every queue. A process killed
        # outright is caught by the next run instead (route_unfinished).
        if audit is not None:
            try:
                route_unfinished(complaint, audit)
            except Exception:
                pass
        raise


def _prepare(
    complaint: Complaint,
    scope: str,
    narrative: str | None,
    public: str | None,
    signals: list[str],
    redactions: dict[str, int],
    registry: Registry,
    client: anthropic.Anthropic,
    audit: AuditLog | None,
    agentic: bool = False,
    limits: DeskLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ComplaintResult:
    scripted = is_scripted(client)
    calls: list[AgentCall] = []
    pipeline_reasons = {"text_addresses_ai"} if signals else set()
    triage: ComplaintTriage | None = None
    stopped: str | None = None

    def keep(call: AgentCall | None) -> None:
        if call is None:
            return
        calls.append(call)
        if audit is not None:
            payload = call.model_dump(mode="json")
            payload["input_hmac"] = audit.keyed_hash(payload.pop("input_sha256"))
            if scripted:
                # The hall pass's model is recorded, but the answer came from a stand-in.
                payload["scripted"] = True
            audit.append(scope, "agent_call", call.agent_id, payload)

    content = render_complaint(complaint, narrative, public)
    triages: list[ComplaintTriage] = []
    steps: list[DeskStep] = []

    def record_step(step: DeskStep) -> None:
        steps.append(step)
        if audit is not None:
            # Codes, counts and amounts only (DeskStep has no free-text field).
            audit.append(scope, "supervisor_step", SUPERVISOR, step.model_dump(mode="json"))

    def body() -> None:
        if agentic:
            pipeline_reasons.update(
                _supervise(
                    complaint,
                    scope,
                    content,
                    signals,
                    registry,
                    client,
                    calls,
                    keep,
                    triages,
                    record_step,
                    limits or DeskLimits(),
                    clock,
                )
            )
        else:
            run = run_complaint_triage(complaint, content, registry, client, scope)
            keep(run.call)
            triages.append(run.output)

    try:
        body()
    except DeskStopped as exc:
        stopped = str(exc)
    except AgentRefusedError as exc:
        keep(exc.call)
        stopped = "agent refused or returned no usable output"
    except RegistryError as exc:
        keep(getattr(exc, "partial_call", None))
        stopped = f"hall pass refused ({type(exc).__name__})"
    except (anthropic.APIError, ValidationError) as exc:
        keep(getattr(exc, "partial_call", None))
        stopped = f"{type(exc).__name__} from the model call"
    except Exception as exc:  # anything unforeseen goes to a person; the type only
        keep(getattr(exc, "partial_call", None))
        stopped = f"unexpected {type(exc).__name__}"
    triage = triages[0] if triages else None
    if stopped is not None or (triage is None and not agentic):
        pipeline_reasons.add("agent_failed")

    route = route_triages(complaint, triages, pipeline_reasons)
    draft_sha256, problems = None, []
    if triage is not None and triage.draft_acknowledgement.strip():
        problems = draft_problems(triage.draft_acknowledgement)
        if not problems and audit is not None:
            draft_sha256 = store_draft(
                audit,
                DraftResponse(
                    complaint_id=scope,
                    text=triage.draft_acknowledgement,
                    written_by=TRIAGE_AGENT,  # not calls[-1]: the supervisor may have answered last
                    prompt_version=PROMPT_VERSION,
                ),
            )
    cost, unknown = _cost(calls)
    if unknown:
        stopped = unknown if stopped is None else f"{stopped}; {unknown}"
        if "agent_failed" not in route.reasons:
            route = route_triages(complaint, triages, pipeline_reasons | {"agent_failed"})
    result = ComplaintResult(
        complaint_id=complaint.complaint_id,
        scope_id=scope,
        triage=triage,
        route=route,
        draft_sha256=draft_sha256,
        draft_problems=problems,
        injection_signals=signals,
        redactions=redactions,
        agent_calls=calls,
        stopped_reason=stopped,
        cost_usd=cost,
        mode="agentic" if agentic else "fixed",
        triage_runs=len(triages),
        supervisor_steps=steps,
    )
    if audit is not None:
        audit.append(
            scope,
            "complaint_routed",  # ROUTED, written out so the register test can read it
            "pipeline",
            {
                "queue": route.queue,
                "reasons": route.reasons,
                "urgency_floor": route.urgency_floor.value,
                "urgency": route.urgency.value,
                "agent_urgency": None if triage is None else triage.urgency.value,
                "category": None if triage is None else triage.category.value,
                "confidence": None if triage is None else triage.confidence.value,
                "concerns": [] if triage is None else sorted(c.value for c in triage.concerns),
                "summary_hmac": None
                if triage is None
                else audit.keyed_hash(json.dumps(triage.summary)),
                "draft_sha256": draft_sha256,
                "draft_problems": problems,
                "stopped_reason": stopped,
                "cost_usd": cost,
                "models": sorted(
                    {f"{c.agent_id}:{'scripted' if scripted else c.model}" for c in calls}
                ),
                "scripted": scripted,
                "triage_runs": len(triages),
                "supervisor_path": [step.action.value for step in steps],
            },
            once=True,
        )
    return result


def route_triages(
    complaint: Complaint, triages: list[ComplaintTriage], pipeline_reasons: set[str]
) -> ComplaintRoute:
    """The route with every triage's answer counted: each can add reasons and raise urgency,
    none can remove either, and two triages that disagree send the complaint to specialist
    review (triage_disagreed). With no triage, route_complaint's own rule."""
    routes = [route_complaint(complaint, t, pipeline_reasons) for t in triages] or [
        route_complaint(complaint, None, pipeline_reasons)
    ]
    reasons = set().union(*(r.reasons for r in routes))
    if (
        len(triages) > 1
        and len({(t.category, t.urgency, frozenset(t.concerns)) for t in triages}) > 1
    ):
        reasons.add("triage_disagreed")
    urgency = max((r.urgency for r in routes), key=lambda u: u.rank)
    if not triages and reasons & {"triage_skipped", "supervisor_handed_off"}:
        # The supervisor ended before any agent read the complaint: no triage could raise its
        # urgency, so it goes at the highest (found by the offline eval, ADR 0028).
        urgency = Urgency.HIGH
    ordered = sorted(reasons)
    return ComplaintRoute(
        queue="specialist_review" if ordered else "standard_queue",
        reasons=ordered,
        explained=[REASONS[r] for r in ordered],
        urgency_floor=routes[0].urgency_floor,
        urgency=urgency,
    )


def _desk_refusal(decision: DeskDecision, triages: list[ComplaintTriage]) -> StepRefusal | None:
    match decision.action:
        case DeskAction.RUN_TRIAGE:
            return StepRefusal.REPEAT_WITHOUT_CHANGE if triages else None
        case DeskAction.RECHECK_TRIAGE:
            if not triages:
                return StepRefusal.PRECONDITION_UNMET
            return StepRefusal.REPEAT_WITHOUT_CHANGE if len(triages) >= MAX_TRIAGES else None
        case _:
            return None


def _supervise(
    complaint: Complaint,
    scope: str,
    content: str,
    signals: list[str],
    registry: Registry,
    client: anthropic.Anthropic,
    calls: list[AgentCall],
    keep: Callable[[AgentCall | None], None],
    triages: list[ComplaintTriage],
    record_step: Callable[[DeskStep], None],
    limits: DeskLimits,
    clock: Callable[[], float],
) -> set[str]:
    """The desk's agentic loop. Code checks, before each supervisor turn, the step budget; before
    each model call, cost and time; before the chosen step runs, the supervisor's hall pass; and
    before routing, every agent's. Returns the pipeline reasons the supervisor's ending adds:
    supervisor_handed_off for a hand-off, triage_skipped if it ended before any triage ran.
    Raises DeskStopped (budget, refusals) or the agent's own error; the caller sends the
    complaint to specialist review as agent_failed."""
    started = clock()
    refusals = 0
    reasons = code_reasons_for(complaint, signals)
    last: DeskStep | None = None

    def spend() -> None:
        cost = _cost(calls)[0]
        if cost >= limits.max_cost_usd:
            raise DeskStopped(f"budget exhausted: cost reached {limits.max_cost_usd} USD")
        if clock() - started >= limits.max_seconds:
            raise DeskStopped(f"budget exhausted: {limits.max_seconds:g} seconds")

    def record(decision: DeskDecision, outcome: StepOutcome, refusal=None, agents=()):
        nonlocal last
        last = DeskStep(
            step=(last.step + 1) if last else 1,
            action=decision.action,
            reason=decision.reason,
            outcome=outcome,
            refusal=refusal,
            agents=list(agents),
            cost_usd=_cost(calls)[0],
            seconds=round(clock() - started, 2),
        )
        record_step(last)

    while True:
        taken = last.step if last else 0
        if taken >= limits.max_steps:
            raise DeskStopped(f"budget exhausted: {limits.max_steps} supervisor steps")
        spend()
        view = DeskView(
            code_reasons=sorted(reasons),
            urgency_floor=urgency_floor(reasons),
            injection_signals=len(signals),
            triages=[
                _Triage(
                    category=t.category,
                    urgency=t.urgency,
                    confidence=t.confidence,
                    concerns=list(t.concerns),
                )
                for t in triages
            ],
            max_triages=MAX_TRIAGES,
            last_step=None
            if last is None
            else _LastStep(action=last.action, outcome=last.outcome, refusal=last.refusal),
            budget_left=_Budget(
                steps=limits.max_steps - taken,
                cost_usd=round(max(limits.max_cost_usd - _cost(calls)[0], 0.0), 6),
                seconds=round(max(limits.max_seconds - (clock() - started), 0.0), 1),
            ),
        )
        run = run_desk_supervisor(scope, view, registry, client)
        keep(run.call)
        decision = run.output
        if decision.action in (DeskAction.FINISH, DeskAction.HAND_OFF):
            try:
                for agent_id in dict.fromkeys(c.agent_id for c in calls):
                    registry.require(agent_id)
            except Exception:
                record(decision, StepOutcome.FAILED)
                raise
            handed = decision.action is DeskAction.HAND_OFF
            record(decision, StepOutcome.HANDED_OFF if handed else StepOutcome.FINISHED)
            ending = {"supervisor_handed_off"} if handed else set()
            return ending | (set() if triages else {"triage_skipped"})
        refusal = _desk_refusal(decision, triages)
        if refusal is not None:
            refusals += 1
            record(decision, StepOutcome.REFUSED, refusal)
            if refusals >= MAX_REFUSALS:
                raise DeskStopped(f"supervisor loop: {MAX_REFUSALS} steps refused")
            continue
        try:
            registry.require(SUPERVISOR)  # a stop made since it chose ends the step here
            spend()
            triage_run = run_complaint_triage(complaint, content, registry, client, scope)
        except Exception:
            record(decision, StepOutcome.FAILED, agents=["complaint_triage"])
            raise
        keep(triage_run.call)
        triages.append(triage_run.output)
        record(decision, StepOutcome.RAN, agents=["complaint_triage"])


def code_reasons_for(complaint: Complaint, signals: list[str]) -> set[str]:
    """The code's reasons before any agent: CFPB's labels and tags, and the injection flag."""
    return _code_reasons(complaint) | ({"text_addresses_ai"} if signals else set())


def route_unfinished(complaint: Complaint, audit: AuditLog) -> ComplaintRoute:
    """Route a complaint whose triage started (its received entry is in the log) but never
    finished: to specialist review, as agent_failed and triage_interrupted, with the code's own
    reasons. Raises AlreadyRecorded if it was routed meanwhile."""
    route = route_complaint(complaint, None, {"agent_failed", "triage_interrupted"})
    audit.append(
        scope_id(complaint.complaint_id),
        "complaint_routed",  # ROUTED
        "pipeline",
        {
            "queue": route.queue,
            "reasons": route.reasons,
            "urgency_floor": route.urgency_floor.value,
            "urgency": route.urgency.value,
            "agent_urgency": None,
            "category": None,
            "confidence": None,
            "concerns": [],
            "summary_hmac": None,
            "draft_sha256": None,
            "draft_problems": [],
            "stopped_reason": "triage was interrupted before it was routed",
            "cost_usd": None,
            "models": [],
            "scripted": None,
        },
        once=True,
    )
    return route
