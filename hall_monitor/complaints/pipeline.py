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
from collections import Counter

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRefusedError
from hall_monitor.complaints.drafts import DraftResponse, store_draft
from hall_monitor.complaints.models import Complaint
from hall_monitor.complaints.routing import ComplaintRoute, route_complaint
from hall_monitor.complaints.triage_agent import (
    PROMPT_VERSION,
    ComplaintTriage,
    render_complaint,
    run_complaint_triage,
)
from hall_monitor.gateway.cost import UnpricedModelError, call_cost
from hall_monitor.gateway.injection import detect_in_texts
from hall_monitor.gateway.redaction import redact_text
from hall_monitor.governance.audit import AlreadyRecorded, AuditLog
from hall_monitor.models import AgentCall
from hall_monitor.registry import Registry, RegistryError

ROUTED = "complaint_routed"
RECEIVED = "complaint_received"

# A draft is for a person to adapt, not a commitment: one that promises money or an outcome, or
# carries a way to reach someone, is not kept.
_DRAFT_PROBLEMS: dict[str, re.Pattern[str]] = {
    "promises_an_outcome": re.compile(
        r"\b(?:refund|reimburs|compensat|guarantee|"
        r"(?:we will|we'll|we shall|we are going to) (?!review|look|be in touch)\w+|"
        r"will be (?:credited|waived|removed|refunded|reversed|corrected|deleted|closed|paid|"
        r"fixed|resolved)|has been (?:resolved|refunded|credited|waived|corrected)|"
        r"within \d+ (?:business |working )?(?:days?|hours?|weeks?))",
        re.I,
    ),
    "has_a_link": re.compile(
        r"https?://|www\.|\b[a-z0-9-]+\.(?:com|org|net|gov|io|co|us|uk|info|biz)\b", re.I
    ),
    "has_an_email_address": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "has_a_phone_number": re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?(?:[\s.-]?\d{3,4}){2}\b"),
}


class AlreadyTriaged(ValueError):
    pass


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
    scripted: bool = False,
) -> ComplaintResult:
    """scripted: the client is the scripted stand-in (complaints/scripted.py); the routed entry
    says so, so a log never shows its answers as the model's."""
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
            audit.append(scope, "agent_call", call.agent_id, payload)

    content = render_complaint(complaint, narrative, public)
    try:
        run = run_complaint_triage(complaint, content, registry, client, scope)
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
    else:
        keep(run.call)
        triage = run.output
    if triage is None:
        pipeline_reasons.add("agent_failed")

    route = route_complaint(complaint, triage, pipeline_reasons)
    draft_sha256, problems = None, []
    if triage is not None and triage.draft_acknowledgement.strip():
        problems = draft_problems(triage.draft_acknowledgement)
        if not problems and audit is not None:
            draft_sha256 = store_draft(
                audit,
                DraftResponse(
                    complaint_id=scope,
                    text=triage.draft_acknowledgement,
                    written_by=calls[-1].agent_id,
                    prompt_version=PROMPT_VERSION,
                ),
            )
    cost, unknown = _cost(calls)
    if unknown:
        stopped = unknown if stopped is None else f"{stopped}; {unknown}"
        if "agent_failed" not in route.reasons:
            route = route_complaint(complaint, triage, pipeline_reasons | {"agent_failed"})
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
                "models": sorted({f"{c.agent_id}:{c.model}" for c in calls}),
                "scripted": scripted,
            },
            once=True,
        )
    return result
