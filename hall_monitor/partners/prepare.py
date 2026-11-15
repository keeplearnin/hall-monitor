"""Prepare one partner claim for a person: the agents read and flag, code checks and routes
(HM-37, ADR 0033).

1. The documents are redacted before any agent sees them.
2. The intake agent extracts the invoice and dates. If it fails, or returns nothing usable, the
   claim goes to a person with nothing reimbursable: no value is invented to keep going.
3. Code checks every extracted value against the documents; a value that isn't there adds
   ``extraction_unsupported``.
4. The rules (slice 1) assess the claim built from the portal's fields and the extraction.
5. The proof reviewer raises concerns from a closed list. Each maps to a fixed reason that can
   only add review: the tier and the role only go up, and no rule finding is ever removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import anthropic
from pydantic import ValidationError

from hall_monitor.agents.base import AgentRefusedError
from hall_monitor.gateway.redaction import redact_text
from hall_monitor.models import AgentCall
from hall_monitor.partners.documents import (
    AGENT_REASONS,
    CONCERN_REASONS,
    check_extraction,
    claim_from_extraction,
)
from hall_monitor.partners.intake_agent import run_claim_intake
from hall_monitor.partners.models import Documents, EvidenceItem, ProofReview, Submission
from hall_monitor.partners.review_agent import run_proof_review
from hall_monitor.partners.rules import (
    ROUTES,
    Assessment,
    ClaimBook,
    assess,
    portal_reasons,
    route_of,
)
from hall_monitor.registry import Registry, RegistryError

CENT = Decimal("0.01")

# What a governed agent call raises when it gives no usable answer: a refusal or incomplete turn,
# a refused hall pass or a stop, an API error, output that fails its type. Anything else is our
# own bug and is raised, not passed off as an agent failure (review 37).
AGENT_FAILURES = (AgentRefusedError, RegistryError, anthropic.APIError, ValidationError)


@dataclass
class Prepared:
    """What a person sees: the assessment, the reviewer's answer (None when it wasn't asked or
    gave none) and every agent call, for the record."""

    assessment: Assessment
    review: ProofReview | None = None
    calls: list[AgentCall] = field(default_factory=list)


def _redacted(documents: Documents) -> Documents:
    return Documents(
        invoice=redact_text(documents.invoice),
        evidence=[EvidenceItem(type=e.type, text=redact_text(e.text)) for e in documents.evidence],
    )


def _call_of(exc: BaseException) -> AgentCall | None:
    return getattr(exc, "call", None) or getattr(exc, "partial_call", None)


def _unread(submission: Submission, documents: Documents, book: ClaimBook) -> Assessment:
    """Nobody could read the documents: a person reads them, nothing is reimbursable yet, and
    every finding code can make without the invoice is kept (review 37)."""
    reasons = {"extraction_failed"} | portal_reasons(
        partner_id=submission.partner_id,
        preapproval_id=submission.preapproval_id,
        activity_type=submission.activity_type,
        evidence=submission.evidence,
        payee_account=submission.payee_account,
        document_text=[documents.invoice, *(item.text for item in documents.evidence)],
        book=book,
    )
    tier, role = route_of(reasons, submission.claimed_usd, book.program, AGENT_REASONS)
    return Assessment(
        claim_id=submission.id,
        tier=tier,
        route=ROUTES[tier],
        decided_by=role.label,
        reasons=sorted(reasons),
        reimbursable_usd=Decimal("0.00"),
        held_usd=submission.claimed_usd.quantize(CENT),
    )


def _with(assessment: Assessment, added: set[str], claimed_usd, book: ClaimBook) -> Assessment:
    """The rules' assessment with the agents' reasons added. The tier and role are worked out
    again from all the reasons together, so they only go up; the money is the rules'."""
    if not added:
        return assessment
    reasons = set(assessment.reasons) | added
    tier, role = route_of(reasons, claimed_usd, book.program, AGENT_REASONS)
    return assessment.model_copy(
        update={
            "tier": tier,
            "route": ROUTES[tier],
            "decided_by": role.label,
            "reasons": sorted(reasons),
        }
    )


def prepare(
    submission: Submission,
    documents: Documents,
    book: ClaimBook,
    *,
    registry: Registry,
    client: anthropic.Anthropic | None = None,
) -> Prepared:
    calls: list[AgentCall] = []
    redacted = _redacted(documents)

    try:
        run = run_claim_intake(submission.id, redacted, registry, client)
    except AGENT_FAILURES as exc:  # all go to a person
        if (call := _call_of(exc)) is not None:
            calls.append(call)
        return Prepared(_unread(submission, documents, book), None, calls)
    calls.append(run.call)
    extraction = run.output

    problems = check_extraction(extraction, redacted)
    if "nothing_extracted" in problems:
        return Prepared(_unread(submission, documents, book), None, calls)
    try:
        claim = claim_from_extraction(submission, extraction, documents)
    except ValidationError:  # an end before the start, say
        return Prepared(_unread(submission, documents, book), None, calls)

    others = [c for c in book.claims if c.id != claim.id]
    assessment = assess(claim, book.model_copy(update={"claims": [*others, claim]}))
    added = {"extraction_unsupported"} if problems else set()

    review: ProofReview | None = None
    try:
        reviewed = run_proof_review(
            submission.id, submission.activity_type, extraction, redacted, registry, client
        )
    except AGENT_FAILURES as exc:  # a person checks the proof
        if (call := _call_of(exc)) is not None:
            calls.append(call)
        added.add("review_failed")
    else:
        calls.append(reviewed.call)
        review = reviewed.output
        added |= {CONCERN_REASONS[c] for c in review.concerns}
        if review.confidence == "low":
            added.add("review_uncertain")

    return Prepared(_with(assessment, added, submission.claimed_usd, book), review, calls)
