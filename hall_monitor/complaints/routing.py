"""Who looks at a complaint first, and how urgently: a code rule, not a model.

Every complaint goes to a person; nothing is resolved automatically. A complaint with any reason
below goes to specialist review, the rest to the standard queue. The rule reads CFPB's own
structured fields (product, issue, sub-issue, tags, timeliness) and the pipeline's checks; the
agent's answer can add reasons and raise urgency, never remove a reason or lower urgency below
the code's floor.

Matching is on CFPB's fixed labels, case-insensitive. It is deliberately broad: a complaint
wrongly sent to specialist review costs a person's time, one wrongly kept off it can cost a
consumer their home.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from hall_monitor.complaints.models import Complaint, Tag
from hall_monitor.complaints.triage_agent import ComplaintTriage, Confidence, Urgency

# Every reason the rule gives, and what it means.
REASONS: dict[str, str] = {
    "fraud": "fraud, a scam, or a transaction or charge the consumer didn't authorise or make",
    "identity_theft": (
        "identity theft, or an account, loan, debt or inquiry the consumer says isn't theirs"
    ),
    "foreclosure": "foreclosure, or a mortgage the consumer is struggling to pay",
    "discrimination": "discrimination is named",
    "older_american": "tagged Older American",
    "servicemember": "tagged Servicemember",
    "untimely": "CFPB shows the company's response as untimely",
    "text_addresses_ai": "the complaint contains text aimed at the AI (possible prompt injection)",
    "agent_failed": "the triage agent failed, was stopped, isn't approved or gave no usable answer",
    "agent_uncertain": "the triage agent's confidence was low, or it couldn't tell the category",
    "agent_disagrees_with_code": "the agent's urgency is below the code's floor",
    "agent_raised_concern": "the agent raised a concern the code's rule didn't find",
    "triage_interrupted": "triage started but was interrupted before it was routed",
    # The desk supervisor's (ADR 0028): each only adds review.
    "triage_disagreed": "two independent triages of the complaint disagreed",
    "supervisor_handed_off": "the desk supervisor handed the complaint to specialist review",
    "triage_skipped": "the desk supervisor finished before any triage ran",
    "supervisor_failed": (
        "the desk supervisor failed, was stopped, ran out of budget or kept choosing steps code "
        "refused"
    ),
}
# Reasons only the pipeline gives (complaints/pipeline.py), not the rule on the fields.
PIPELINE_REASONS = frozenset(
    {
        "text_addresses_ai",
        "agent_failed",
        "triage_interrupted",
        "triage_disagreed",
        "supervisor_handed_off",
        "triage_skipped",
        "supervisor_failed",
    }
)

# Concerns read from CFPB's labels. Each pattern is matched against the product, sub-product,
# issue and sub-issue.
CONCERN_PATTERNS: dict[str, re.Pattern[str]] = {
    "fraud": re.compile(
        r"\bfraud|\bscam\b|unauthori[sz]ed|not authori[sz]ed|(?:did not|didn't) authori[sz]e|"
        r"(?:did not|didn't) (?:make|purchase)",
        re.I,
    ),
    "identity_theft": re.compile(
        r"identity theft|belongs to someone else|"
        r"opened without (?:my|your|the consumer's) consent|"
        r"\bis not (?:mine|yours)\b|not (?:my|your) (?:debt|account|loan)|"
        r"(?:didn't|did not) apply for|(?:don't|do not) recognize",
        re.I,
    ),
    "foreclosure": re.compile(
        r"foreclos|struggling to pay (?:the |your )?mortgage|deed-in-lieu|short sale", re.I
    ),
    "discrimination": re.compile(r"discriminat", re.I),
}
# Checked against every label in the pinned snapshot by a hand-written table
# (tests/test_complaint_triage.py EXPECTED_LABEL_REASONS), not by these patterns themselves.
# These always raise the floor to high.
HIGH_REASONS = frozenset(
    {"fraud", "identity_theft", "foreclosure", "discrimination", "older_american", "servicemember"}
)


class ComplaintRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: str  # "specialist_review" or "standard_queue"; both are people
    reasons: list[str]  # codes from REASONS, sorted
    explained: list[str]
    urgency_floor: Urgency  # the code's
    urgency: Urgency  # the floor, raised by the agent if it said higher


def code_reasons(complaint: Complaint) -> set[str]:
    """Reasons the code finds in the complaint's own fields, before any agent."""
    labels = " | ".join(
        x
        for x in (complaint.product, complaint.sub_product, complaint.issue, complaint.sub_issue)
        if x
    )
    reasons = {name for name, pattern in CONCERN_PATTERNS.items() if pattern.search(labels)}
    if Tag.OLDER_AMERICAN in complaint.tags:
        reasons.add("older_american")
    if Tag.SERVICEMEMBER in complaint.tags:
        reasons.add("servicemember")
    if complaint.timely is False or complaint.company_response == "Untimely response":
        reasons.add("untimely")
    return reasons


def urgency_floor(reasons: set[str]) -> Urgency:
    if reasons & HIGH_REASONS:
        return Urgency.HIGH
    return Urgency.NORMAL if reasons else Urgency.LOW


def route_complaint(
    complaint: Complaint, triage: ComplaintTriage | None, pipeline_reasons: set[str]
) -> ComplaintRoute:
    """triage is None when the agent gave no usable answer (the pipeline adds agent_failed)."""
    reasons = code_reasons(complaint) | set(pipeline_reasons)
    floor = urgency_floor(reasons)
    urgency = floor
    if triage is not None:
        if triage.confidence is Confidence.LOW or triage.category.value == "unclear":
            reasons.add("agent_uncertain")
        if triage.urgency.rank < floor.rank:
            reasons.add("agent_disagrees_with_code")
        found = {c for c in CONCERN_PATTERNS if c in reasons}
        if {c.value for c in triage.concerns} - found:
            reasons.add("agent_raised_concern")
        if triage.urgency.rank > urgency.rank:
            urgency = triage.urgency
        if triage.concerns:  # every concern is one the code would rate high
            urgency = Urgency.HIGH
    ordered = sorted(reasons)
    return ComplaintRoute(
        queue="specialist_review" if ordered else "standard_queue",
        reasons=ordered,
        explained=[REASONS[r] for r in ordered],
        urgency_floor=floor,
        urgency=urgency,
    )
