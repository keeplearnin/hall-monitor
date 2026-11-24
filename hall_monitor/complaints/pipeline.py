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
A draft that promises an outcome, contact or a deadline, or carries any way to reach someone, is
not kept; the log records why.

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
from hall_monitor.agents.loop import Action, SupervisorLoop, run_supervisor_loop
from hall_monitor.budgets import SpendRefused
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
from hall_monitor.gateway.cost import UnpricedModelError, total_cost
from hall_monitor.gateway.injection import decoded, detect_in_texts
from hall_monitor.gateway.redaction import redact_text
from hall_monitor.governance.audit import AlreadyRecorded, AuditLog
from hall_monitor.registry import Registry, RegistryError
from hall_monitor.shared_models import AgentCall, StepOutcome
from hall_monitor.spend import CaseStart, SpendLedger, finish_case, start_case

ROUTED = "complaint_routed"
RECEIVED = "complaint_received"
# Additive: a later version of a routed complaint (review 25). The first route is never edited.
REROUTED = "complaint_rerouted"

# A draft is for a person to adapt, not a commitment, and is never sent. The check is strict on
# purpose (review 25): one that promises money, an outcome, contact or a deadline, or carries any
# way to reach someone (a link or domain, an email address, a handle, a phone number, an address,
# an extension, a person to ask for) is not kept. Any run of three or more digits is refused: it
# could be a phone number, an address or an extension, and the agent is never given a reference.
_NUMBER_WORDS = r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|a few|few|several)"
_WEEKDAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_DRAFT_PROBLEMS: dict[str, re.Pattern[str]] = {
    "promises_an_outcome": re.compile(
        r"\b(?:refund|reimburs|compensat|guarantee"
        r"|(?:will|'ll) (?:\w+ )?(?:refund|credit|waive|remove|delete|reverse|correct|fix|resolve|"
        r"pay|close|cancel|forgive|update your|adjust)"
        r"|will be (?:credited|waived|removed|refunded|reversed|corrected|deleted|closed|paid|"
        r"fixed|resolved|cancelled|canceled|forgiven|adjusted|contacted|called|sent)"
        r"|has been (?:resolved|refunded|credited|waived|corrected|fixed)"
        # will / shall / going to + an outcome or contact, up to two words between.
        r"|(?:will|shall|'ll|going to|are to|is to)\s+(?:\w+\s+){0,2}?"
        r"(?:receive|get|refund|reverse|credit|respond|reply|contact|call|phone|email|e-mail|"
        r"text|write|send|mail|reach|return|reimburse|issue|restore|make (?:this|it) right|"
        r"put (?:the|your) money back)\b"
        r"|money back|full credit"
        # Deadlines.
        rf"|within (?:the next )?{_NUMBER_WORDS}\s+(?:business |working |calendar )?"
        r"(?:days?|hours?|weeks?|months?)"
        rf"|\b(?:by|before|until) (?:next |this |the end of |end of )?"
        rf"(?:{_WEEKDAYS}|tomorrow|today|tonight|end of (?:the )?(?:day|week|month)|"
        r"close of business)"
        r"|\b(?:in|over) the (?:next|coming) (?:few )?(?:days?|hours?|weeks?))",
        re.I,
    ),
    "has_a_link": re.compile(
        # https://, www., or any domain-shaped token with any top-level domain.
        r"https?://|www\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24}\b"
        # Spelled out: "example dot com", "colon slash slash". A draft has no reason to say
        # "dot" between two words.
        r"|\bcolon\s+slash\s+slash\b|\b[a-z0-9-]+\s+dot\s+[a-z]{2,24}\b",
        re.I,
    ),
    "has_an_email_address": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "has_a_handle": re.compile(r"(?<![\w.])@[A-Za-z0-9_]{2,}"),
    # Digits in groups, as phone numbers are written; any longer run is caught below.
    "has_a_phone_number": re.compile(
        r"(?:\+\d{1,3}[\s.-]?)?\(?\b\d{2,4}\)?(?:[\s.-]\d{3,4}){2}\b|\b\d{3}[\s.-]\d{4}\b"
        r"|\b1[\s.-]?8[0-9]{2}[\s.-]?[A-Z]{4,}"  # vanity numbers: 1-800-FLOWERS
    ),
    "has_a_digit_run": re.compile(r"\d(?:[\s().-]?\d){2,}"),
    "has_an_address": re.compile(
        r"\b\d+\s+(?:\w+\s+){1,3}(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|"
        r"way|court|ct|place|pl|plaza|square|suite|ste|floor|fl)\b"
        r"|\bp\.?\s?o\.?\s+box\b|\b(?:write|mail) (?:to )?us\b|\bsuite \d",
        re.I,
    ),
    "names_someone_to_reach": re.compile(
        r"\bext(?:ension|\.)?\s*#?\s*\d|\bask for\b|\b(?:speak|talk) (?:to|with) \w+ in\b"
        r"|\b(?:dial|call|phone|text|ring) (?:us|me|our|them)\b",
        re.I,
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
    """The checks the draft fails, searched as written and as a reader would see it (entities,
    full-width forms, look-alike letters and invisible characters undone)."""
    seen = decoded(text)
    return sorted(
        name
        for name, pattern in _DRAFT_PROBLEMS.items()
        if pattern.search(text) or pattern.search(seen)
    )


def _cost(calls: list[AgentCall]) -> tuple[float, str | None]:
    try:
        return round(total_cost(calls), 6), None
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
    spend: SpendLedger | None = None,
) -> ComplaintResult:
    """agentic=True (the default) lets the desk supervisor choose the steps within code's
    bounds (ADR 0028); agentic=False runs the old fixed order, one triage.

    Before any model call the desk's approved budget is read from registry/budgets.yaml, and
    with spend (the ledger the live entry points pass) the complaint's cost is reserved against
    the complaints daily and monthly caps (HM-34, ADR 0030). A refusal sends it to specialist
    review as spend_refused, with no model call; limits overrides the case budget (tests).

    The routed entry says whether the answer came from the scripted stand-in, read from the
    client itself (complaints/scripted.py), so a log never shows its answers as the model's."""
    scope = scope_id(complaint.complaint_id)
    if audit is not None and audit.has(scope, RECEIVED):
        raise AlreadyTriaged(f"the audit log shows {scope} was already triaged")
    narrative, public, redactions, signals = _scan(complaint)
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
                    # Keyed: a plain hash of a typed-in narrative could be confirmed by
                    # guessing it (Review 46).
                    "content_hmac": audit.keyed_hash(complaint.content_body()),
                    "has_narrative": bool(complaint.narrative),
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
            limits,
            clock or time.monotonic,
            spend,
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


def _scan(complaint: Complaint) -> tuple[str | None, str | None, dict[str, int], list[str]]:
    """The redacted free text, the redaction counts and the injection signals. Every field the
    agent is sent is scanned, fixed-list labels included, raw and redacted."""
    counts: Counter[str] = Counter()
    narrative = redact_text(complaint.narrative, counts) if complaint.narrative else None
    public = (
        redact_text(complaint.company_public_response, counts)
        if complaint.company_public_response
        else None
    )
    redactions = {k: v for k, v in sorted(counts.items()) if v}
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
    return narrative, public, redactions, signals


def reroute_changed(
    complaint: Complaint, audit: AuditLog, summary: dict | None = None
) -> ComplaintRoute | None:
    """Re-route a routed complaint whose stored version changed in a way a person must see
    (review 25, narrowed in review 38b): the new version adds a code reason no earlier route
    gave (CFPB adds a Servicemember tag or marks the response untimely), raises the urgency
    floor, or adds a narrative where the routed version had none. None if it isn't routed yet,
    this version is the one last routed, or the change is routine (a company_response status
    moving from "In progress" to "Closed with explanation", a dropped tag): then nothing is
    written and the earlier route stands; with summary, it is counted as changed_not_rerouted.

    The first route is never edited: an additive complaint_rerouted entry sends it to specialist
    review as record_changed, with the new version's code reasons and every reason an earlier
    route gave, at the higher of the earlier urgency and the new floor. No agent is called: the
    triage answered about another version, and a person reads this one. Written once per
    version (the log refuses a second complaint_rerouted with the same content hash inside its
    write transaction), so two runs at the same moment re-route it once. Keyed hashes and codes
    only."""
    scope = scope_id(complaint.complaint_id)
    entries = audit.entries(scope)
    received = [e["payload"] for e in entries if e["event"] == RECEIVED]
    routes = [e["payload"] for e in entries if e["event"] in (ROUTED, REROUTED)]
    if not received or not routes:
        return None
    rerouted = [e["payload"] for e in entries if e["event"] == REROUTED]
    last = rerouted[-1] if rerouted else received[0]
    digest = audit.keyed_hash(complaint.content_body())
    # Entries written before Review 46 hold a plain hash; compared the same way it was made.
    seen = last.get("content_hmac")
    plain = last.get("content_sha256")
    unchanged = seen == digest if isinstance(seen, str) else plain == complaint.content_sha256()
    if not isinstance(seen, str) and not isinstance(plain, str) or unchanged:
        # Unchanged, or a received entry with no hash to compare (a killed run's): that one
        # was routed to specialist review as triage_interrupted.
        return None
    seen = seen if isinstance(seen, str) else None
    _, _, redactions, signals = _scan(complaint)
    new = code_reasons_for(complaint, signals)
    before = {r for p in routes for r in p.get("reasons") or [] if r in REASONS}
    floor = urgency_floor(new)
    known = {u.value for u in Urgency}
    floors = [Urgency(p["urgency_floor"]) for p in routes if p.get("urgency_floor") in known]
    # An entry written before has_narrative was recorded: unknown, so a narrative counts as new.
    narrative_added = bool(complaint.narrative) and not last.get("has_narrative", False)
    added = new - before
    raised = not floors or floor.rank > max(f.rank for f in floors)
    if not (added or raised or narrative_added):
        if summary is not None:
            summary["changed_not_rerouted"] = summary.get("changed_not_rerouted", 0) + 1
        return None
    reasons = sorted(before | new | {"record_changed"})
    urgencies = [floor] + [Urgency(p["urgency"]) for p in routes if p.get("urgency") in known]
    route = ComplaintRoute(
        queue="specialist_review",
        reasons=reasons,
        explained=[REASONS[r] for r in reasons],
        urgency_floor=floor,
        urgency=max(urgencies, key=lambda u: u.rank),
    )
    try:
        audit.append(
            scope,
            "complaint_rerouted",  # REROUTED, written out so the register test can read it
            "pipeline",
            {
                "complaint_id": complaint.complaint_id,
                "content_hmac": digest,
                "previous_content_hmac": seen,  # None after a pre-Review-46 entry
                "queue": route.queue,
                "reasons": route.reasons,
                "new_code_reasons": sorted(added),
                "narrative_added": narrative_added,
                "has_narrative": bool(complaint.narrative),
                "urgency_floor": route.urgency_floor.value,
                "urgency": route.urgency.value,
                "identifiers_redacted": redactions,
                "injection_signals": signals,
            },
            once_per="content_hmac",
        )
    except AlreadyRecorded:  # another run re-routed this version at the same moment
        return None
    return route


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
    spend: SpendLedger | None = None,
) -> ComplaintResult:
    scripted = is_scripted(client)
    start: CaseStart | None = None
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
        nonlocal start
        # Before any model call: the approved case budget and, with a ledger, the complaint's
        # reservation against the desk's caps (HM-34).
        start = start_case("complaints", scope, spend)
        case = start.budget
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
                    limits or DeskLimits(case.max_steps, case.max_cost_usd, case.max_seconds),
                    clock,
                )
            )
        else:
            run = run_complaint_triage(complaint, content, registry, client, scope)
            keep(run.call)
            triages.append(run.output)

    discard = False

    def failed_in(exc: BaseException) -> str:
        """Which part failed: on the agentic path, anything but the triage call itself is the
        supervisor's loop (review 23), so the reason doesn't blame the triage agent. A stop on
        the triage agent after it answered marks its answers for discarding (review 25)."""
        nonlocal discard
        discard = discard or getattr(exc, "triage_stopped", False) is True
        if not agentic or getattr(exc, "desk_stage", None) == "triage":
            return "agent_failed"
        return "supervisor_failed"

    failure = None
    try:
        body()
    except SpendRefused as exc:  # a fixed template: budgets not approved, or a cap reached
        stopped, failure = str(exc), "spend_refused"
    except DeskStopped as exc:
        stopped, failure = str(exc), "supervisor_failed"
    except AgentRefusedError as exc:
        keep(exc.call)
        stopped, failure = "agent refused or returned no usable output", failed_in(exc)
    except RegistryError as exc:
        keep(getattr(exc, "partial_call", None))
        stopped, failure = f"hall pass refused ({type(exc).__name__})", failed_in(exc)
    except (anthropic.APIError, ValidationError) as exc:
        keep(getattr(exc, "partial_call", None))
        stopped, failure = f"{type(exc).__name__} from the model call", failed_in(exc)
    except Exception as exc:  # anything unforeseen goes to a person; the type only
        keep(getattr(exc, "partial_call", None))
        stopped, failure = f"unexpected {type(exc).__name__}", failed_in(exc)
    if failure == "supervisor_failed":
        stopped = f"desk supervisor: {stopped}"
    triage_runs = len(triages)
    if discard and triages:
        # The triage agent was stopped after it answered: a stop says its output can't be
        # trusted, so no answer of it routes the complaint or leaves a draft. With no triage the
        # agentic route is specialist review at high urgency (HR's _end does the same).
        triages.clear()
        stopped = f"{stopped}; the stopped triage agent's answer was discarded"
    triage = triages[0] if triages else None
    if failure is not None:
        pipeline_reasons.add(failure)
    elif triage is None and not agentic:
        pipeline_reasons.add("agent_failed")

    route = route_triages(complaint, triages, pipeline_reasons, agentic=agentic)
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
    # What the complaint cost, freeing the rest of its reservation; unpriced stays reserved.
    finish_case(spend, start, None if unknown else cost)
    if unknown:
        stopped = unknown if stopped is None else f"{stopped}; {unknown}"
        if not {"agent_failed", "supervisor_failed"} & set(route.reasons):
            route = route_triages(
                complaint, triages, pipeline_reasons | {"agent_failed"}, agentic=agentic
            )
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
        triage_runs=triage_runs,
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
                "mode": "agentic" if agentic else "fixed",
                "triage_runs": triage_runs,
                "triage_discarded": discard and triage_runs > 0,
                "supervisor_path": [step.action.value for step in steps],
            },
            once=True,
        )
    return result


def route_triages(
    complaint: Complaint,
    triages: list[ComplaintTriage],
    pipeline_reasons: set[str],
    *,
    agentic: bool = False,
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
    if agentic and not triages:
        # The agentic desk ended before any agent read the complaint, however it ended (a
        # finish or hand-off, a failure, a stop, the budget): no triage could raise its urgency,
        # so it goes at the highest (the offline eval and review 23, ADR 0028). The fixed path
        # keeps the code's floor, as it always has.
        urgency = Urgency.HIGH
    ordered = sorted(reasons)
    return ComplaintRoute(
        queue="specialist_review" if ordered else "standard_queue",
        reasons=ordered,
        explained=[REASONS[r] for r in ordered],
        urgency_floor=routes[0].urgency_floor,
        urgency=urgency,
    )


def desk_action(
    triages: list[ComplaintTriage], decision: DeskDecision, run: Callable[[], list[str]]
) -> Action:
    """The complaint desk's action table. Routing stays in route_triages."""
    match decision.action:
        case DeskAction.RUN_TRIAGE:
            return Action(repeat=lambda: bool(triages), run=run)
        case DeskAction.RECHECK_TRIAGE:
            return Action(
                missing=lambda: not triages,
                repeat=lambda: len(triages) >= MAX_TRIAGES,
                run=run,
            )
        case _:
            return Action(terminal=True)


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
    reasons = code_reasons_for(complaint, signals)
    last: DeskStep | None = None

    def check_agents() -> None:
        """A stop on any agent the complaint has used ends it at the next step (the kill-switch
        drill); the triage agent's stop is its own failure."""
        for agent_id in dict.fromkeys(c.agent_id for c in calls):
            try:
                registry.require(agent_id)
            except Exception as exc:
                if agent_id != SUPERVISOR:
                    exc.desk_stage = "triage"  # type: ignore[attr-defined]
                    # It answered before the stop: the caller discards its answers.
                    exc.triage_stopped = True  # type: ignore[attr-defined]
                raise

    def spend() -> None:
        cost, unknown = _cost(calls)
        if unknown is not None:  # an unpriced model would read as free: stop (review 23)
            raise DeskStopped(f"budget can't be checked: {unknown}")
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

    def ask() -> DeskDecision:
        taken = last.step if last else 0
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
        return run.output

    def read() -> list[str]:
        triage_run = run_complaint_triage(complaint, content, registry, client, scope)
        keep(triage_run.call)
        triages.append(triage_run.output)
        return ["complaint_triage"]

    def fail_run(decision: DeskDecision, exc: Exception) -> None:
        exc.desk_stage = "triage"  # type: ignore[attr-defined]
        record(decision, StepOutcome.FAILED, agents=["complaint_triage"])

    def finish(decision: DeskDecision) -> set[str]:
        check_agents()
        handed = decision.action is DeskAction.HAND_OFF
        record(decision, StepOutcome.HANDED_OFF if handed else StepOutcome.FINISHED)
        ending = {"supervisor_handed_off"} if handed else set()
        return ending | (set() if triages else {"triage_skipped"})

    return run_supervisor_loop(
        SupervisorLoop(
            steps_taken=lambda: last.step if last else 0,
            max_steps=limits.max_steps,
            cost_usd=lambda: _cost(calls)[0],
            max_cost_usd=limits.max_cost_usd,
            elapsed=lambda: clock() - started,
            max_seconds=limits.max_seconds,
            priced=True,
            unknown_cost=lambda: _cost(calls)[1],
            recheck=check_agents,
            ask=ask,
            action=lambda decision: desk_action(triages, decision, read),
            record=record,
            authorize=lambda decision: registry.require(SUPERVISOR),
            before_work=lambda decision: spend(),
            fail_guard=lambda decision, exc: record(decision, StepOutcome.FAILED),
            fail_run=fail_run,
            finish=finish,
            fail_finish=lambda decision, exc: record(decision, StepOutcome.FAILED),
            stopped=DeskStopped,
        )
    )


def code_reasons_for(complaint: Complaint, signals: list[str]) -> set[str]:
    """The code's reasons before any agent: CFPB's labels and tags, and the injection flag."""
    return _code_reasons(complaint) | ({"text_addresses_ai"} if signals else set())


def route_unfinished(complaint: Complaint, audit: AuditLog) -> ComplaintRoute:
    """Route a complaint whose triage started (its received entry is in the log) but never
    finished: to specialist review, as agent_failed and triage_interrupted, with the code's own
    reasons, at high urgency: no triage answer survived to raise it, so the complaint is treated
    as unread, on either path (review 23). Raises AlreadyRecorded if it was routed meanwhile."""
    route = route_complaint(complaint, None, {"agent_failed", "triage_interrupted"})
    route = route.model_copy(update={"urgency": Urgency.HIGH})
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
            "mode": None,
            "cost_usd": None,
            "models": [],
            "scripted": None,
        },
        once=True,
    )
    return route
