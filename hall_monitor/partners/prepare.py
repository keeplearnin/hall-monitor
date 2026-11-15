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
    _TIER_ORDER,
    ROUTES,
    Assessment,
    ClaimBook,
    Role,
    Tier,
    _ladder_role,
    assess,
)
from hall_monitor.registry import Registry

CENT = Decimal("0.01")


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


def _role_of(label: str) -> Role:
    return next(role for role in Role if role.label == label)


def _unread(submission: Submission, book: ClaimBook) -> Assessment:
    """Nobody could read the documents: a person reads them, and nothing is reimbursable yet."""
    tier = AGENT_REASONS["extraction_failed"][1]
    role = max(
        _ladder_role(submission.claimed_usd, book.program), AGENT_REASONS["extraction_failed"][2]
    )
    return Assessment(
        claim_id=submission.id,
        tier=tier,
        route=ROUTES[tier],
        decided_by=role.label,
        reasons=["extraction_failed"],
        reimbursable_usd=Decimal("0.00"),
        held_usd=submission.claimed_usd.quantize(CENT),
    )


def _with(assessment: Assessment, added: set[str]) -> Assessment:
    """The rules' assessment with the agents' reasons added. Tier and role only go up."""
    if not added:
        return assessment
    tiers = [assessment.tier, *(AGENT_REASONS[r][1] for r in added)]
    tier = max(tiers, key=_TIER_ORDER.__getitem__)
    if tier is Tier.COMPLIANCE:
        role = Role.COMPLIANCE_OFFICER
    else:
        role = max([_role_of(assessment.decided_by), *(AGENT_REASONS[r][2] for r in added)])
    return assessment.model_copy(
        update={
            "tier": tier,
            "route": ROUTES[tier],
            "decided_by": role.label,
            "reasons": sorted(set(assessment.reasons) | added),
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
    except Exception as exc:  # a refusal, a stop, a hall-pass or API error: all go to a person
        if (call := _call_of(exc)) is not None:
            calls.append(call)
        return Prepared(_unread(submission, book), None, calls)
    calls.append(run.call)
    extraction = run.output

    problems = check_extraction(extraction, redacted)
    if "nothing_extracted" in problems:
        return Prepared(_unread(submission, book), None, calls)
    try:
        claim = claim_from_extraction(submission, extraction, documents)
    except ValueError:  # pydantic's ValidationError: an end before the start, say
        return Prepared(_unread(submission, book), None, calls)

    others = [c for c in book.claims if c.id != claim.id]
    assessment = assess(claim, book.model_copy(update={"claims": [*others, claim]}))
    added = {"extraction_unsupported"} if problems else set()

    review: ProofReview | None = None
    try:
        reviewed = run_proof_review(
            submission.id, submission.activity_type, extraction, redacted, registry, client
        )
    except Exception as exc:  # any failure: a person checks the proof
        if (call := _call_of(exc)) is not None:
            calls.append(call)
        added.add("review_failed")
    else:
        calls.append(reviewed.call)
        review = reviewed.output
        added |= {CONCERN_REASONS[c] for c in review.concerns}
        if review.confidence == "low":
            added.add("review_uncertain")

    return Prepared(_with(assessment, added), review, calls)
