"""Who decides a partner claim, and what may be reimbursed: code rules, not a model (HM-36).

Every claim goes to a person. The route is compliance review, hold, review or expedited sign-off
(a person signs quickly and the claim is eligible for post-payment sampling); there is no
automatic approval and nothing here pays, approves or changes a payee. Each outcome is a fixed
reason code with a fixed text. The least senior role that may decide is the higher of the
approval ladder for the reimbursable amount and any role a reason demands; a compliance reason
always goes to a compliance officer. Program terms are data (data/synthetic/partners/
program.yaml); the rule table is in docs/partner-claims-design.md.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from hall_monitor.gateway.injection import detect_in_texts
from hall_monitor.partners.models import Claim, PaidClaim, Partner, PreApproval, Program

CENT = Decimal("0.01")


class Role(IntEnum):
    PARTNER_MARKETING_MANAGER = 1
    REGIONAL_MARKETING_LEAD = 2
    FINANCE_CONTROLLER = 3
    COMPLIANCE_OFFICER = 4

    @property
    def label(self) -> str:
        return self.name.lower()


class Tier(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    COMPLIANCE = "compliance"


_TIER_ORDER = {Tier.GREEN: 0, Tier.YELLOW: 1, Tier.RED: 2, Tier.COMPLIANCE: 3}

# What happens next for each tier. Every one ends with a person.
ROUTES: dict[Tier, str] = {
    Tier.GREEN: "expedited_sign_off",
    Tier.YELLOW: "review",
    Tier.RED: "hold",
    Tier.COMPLIANCE: "compliance_review",
}

PMM, RML, FC = Role.PARTNER_MARKETING_MANAGER, Role.REGIONAL_MARKETING_LEAD, Role.FINANCE_CONTROLLER

# Every reason the rules give: its fixed text, its tier, and the least senior role it demands.
REASONS: dict[str, tuple[str, Tier, Role]] = {
    "partner_not_approved": ("the partner isn't on the approved list", Tier.COMPLIANCE, PMM),
    "public_sector_hospitality": (
        "hospitality with public-sector attendees",
        Tier.COMPLIANCE,
        PMM,
    ),
    "no_preapproval": (
        "no pre-approval for this partner and activity",
        Tier.RED,
        PMM,
    ),
    "activity_before_approval": ("the activity started before it was approved", Tier.RED, FC),
    "claim_late": ("claimed after the claim window closed", Tier.RED, PMM),
    "activity_ineligible": ("an activity type the program doesn't cover", Tier.RED, PMM),
    "invoice_from_partner": ("the invoice was issued by the partner itself", Tier.RED, PMM),
    "duplicate_claim": ("matches another claim's invoice details", Tier.RED, PMM),
    "payee_not_on_master": ("the payee account isn't the partner's approved one", Tier.RED, FC),
    "text_addresses_ai": ("the documents contain text aimed at the AI", Tier.RED, FC),
    "partner_on_ar_hold": ("the partner is on accounts-receivable hold", Tier.RED, PMM),
    "missing_evidence": ("required evidence for the activity is missing", Tier.YELLOW, PMM),
    "excluded_cost": ("the invoice includes costs the program excludes", Tier.YELLOW, PMM),
    "merchandise_over_cap": ("merchandise above the per-unit cap", Tier.YELLOW, PMM),
    "rate_over_benchmark": (
        "a rate above the benchmark by more than the allowed variance; the excess is held",
        Tier.YELLOW,
        PMM,
    ),
    "competitor_content": (
        "competitor products in the creative: the lower reimbursement rate applies",
        Tier.YELLOW,
        PMM,
    ),
    "over_preapproval": ("more than the pre-approved amount", Tier.YELLOW, PMM),
    "claimed_over_invoice": ("claimed more than the invoice total", Tier.YELLOW, PMM),
    "preapproval_used_up": (
        "earlier claims have already used some of this pre-approval",
        Tier.YELLOW,
        PMM,
    ),
    "lines_dont_match_invoice": ("the invoice lines don't add up to its total", Tier.YELLOW, PMM),
    "merchandise_units_missing": ("merchandise without a unit count", Tier.YELLOW, PMM),
    "dates_inconsistent": (
        "the dates are impossible (ends before it starts, or claimed before it ended)",
        Tier.YELLOW,
        PMM,
    ),
    "split_under_threshold": (
        "one of several claims from one partner and agency, each just under an approval limit",
        Tier.YELLOW,
        RML,
    ),
}


class ClaimBook(BaseModel):
    """Everything the rules read: the program, partners, pre-approvals, paid claims and the
    claims being assessed (split claims and duplicates are found across them)."""

    model_config = ConfigDict(extra="forbid")

    program: Program
    partners: list[Partner]
    preapprovals: list[PreApproval]
    history: list[PaidClaim]
    claims: list[Claim]

    @model_validator(mode="after")
    def _references(self) -> ClaimBook:
        partners = {p.id for p in self.partners}
        approvals = {a.id for a in self.preapprovals}
        ids = [c.id for c in self.claims] + [h.id for h in self.history]
        if len(ids) != len(set(ids)):
            raise ValueError("claim IDs must be unique")
        for claim in self.claims:
            if claim.partner_id not in partners:
                raise ValueError(f"claim {claim.id}: unknown partner {claim.partner_id!r}")
            if claim.preapproval_id is not None and claim.preapproval_id not in approvals:
                raise ValueError(f"claim {claim.id}: unknown pre-approval {claim.preapproval_id!r}")
        for step in self.program.approval_ladder:
            Role[step.role.upper()]  # every ladder role is a known role
        return self

    def partner(self, partner_id: str) -> Partner | None:
        """The partner, or None when it isn't in the book (a person looks: never a crash)."""
        return next((p for p in self.partners if p.id == partner_id), None)

    def preapproval(self, preapproval_id: str) -> PreApproval | None:
        return next((a for a in self.preapprovals if a.id == preapproval_id), None)


class Assessment(BaseModel):
    """What the rules found for one claim. A person decides; this only routes and reports."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    tier: Tier
    route: str
    decided_by: str
    reasons: list[str]
    reimbursable_usd: Decimal
    held_usd: Decimal


_LEGAL_SUFFIXES = frozenset(
    "ltd limited inc incorporated llc llp plc gmbh co corp corporation company sa bv pty".split()
)


def _company(name: str) -> str:
    """A company name for comparing: case, punctuation, spacing and legal suffixes ignored."""
    words = re.sub(r"[^0-9a-z]+", " ", name.casefold()).split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _invoice_number(number: str) -> str:
    """An invoice number for comparing: case, punctuation and leading zeros ignored."""
    compact = re.sub(r"[^0-9A-Z]+", "", number.upper())
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", compact)


def _same(a: str, b: str) -> bool:
    return _company(a) == _company(b)


def _keys(invoice, start, end) -> tuple:
    return (_invoice_number(invoice.number), _company(invoice.issuer), invoice.total_usd,
            start, end)  # fmt: skip


def _is_duplicate(claim: Claim, book: ClaimBook) -> bool:
    """At least N of invoice number, issuer, total, start and end match an earlier paid claim or
    another claim in the book, whoever the partner (the same invoice claimed twice)."""
    mine = _keys(claim.invoice, claim.activity_start, claim.activity_end)
    others = [_keys(h.invoice, h.activity_start, h.activity_end) for h in book.history]
    others += [
        _keys(c.invoice, c.activity_start, c.activity_end) for c in book.claims if c.id != claim.id
    ]
    need = book.program.duplicate_fields_to_match
    return any(sum(a == b for a, b in zip(mine, theirs, strict=True)) >= need for theirs in others)


def _thresholds(program: Program) -> list[Decimal]:
    return [s.up_to_usd for s in program.approval_ladder if s.up_to_usd is not None]


def _band_of(amount: Decimal, program: Program) -> Decimal | None:
    """The approval threshold this amount sits just under (within the band), if any."""
    for threshold in _thresholds(program):
        if threshold * program.split.band <= amount <= threshold:
            return threshold
    return None


def _is_split(claim: Claim, book: ClaimBook) -> bool:
    """Part of a group of at least N claims from the same partner and agency, each just under the
    same approval threshold, all submitted within one window (measured across the group, so the
    answer doesn't depend on which claim is asked about)."""
    split = book.program.split
    band = _band_of(claim.claimed_usd, book.program)
    if band is None:
        return False
    dates = [
        c.submitted_on
        for c in book.claims
        if c.partner_id == claim.partner_id
        and _same(c.invoice.issuer, claim.invoice.issuer)
        and _band_of(c.claimed_usd, book.program) == band
    ]
    for start in dates:  # every window that starts at a claim and holds this one
        if not 0 <= (claim.submitted_on - start).days <= split.window_days:
            continue
        inside = [d for d in dates if 0 <= (d - start).days <= split.window_days]
        if len(inside) >= split.min_claims:
            return True
    return False


def _used_before(claim: Claim, approval: PreApproval, book: ClaimBook) -> Decimal:
    """What earlier claims on the same pre-approval have claimed (conservative: claimed, not
    what was reimbursed). Earlier means submitted before, or the same day with a lower ID."""
    return sum(
        (
            c.claimed_usd
            for c in book.claims
            if c.id != claim.id
            and c.preapproval_id == approval.id
            and c.partner_id == claim.partner_id
            and (c.submitted_on, c.id) < (claim.submitted_on, claim.id)
        ),
        Decimal("0"),
    )


def _ladder_role(amount: Decimal, program: Program) -> Role:
    for step in program.approval_ladder:
        if step.up_to_usd is None or amount <= step.up_to_usd:
            return Role[step.role.upper()]
    return Role[program.approval_ladder[-1].role.upper()]


def _approval_for(
    partner_id: str, preapproval_id: str | None, activity_type: str, book: ClaimBook
) -> PreApproval | None:
    if preapproval_id is None:
        return None
    approval = book.preapproval(preapproval_id)
    if (
        approval is None
        or approval.partner_id != partner_id
        or approval.activity_type != activity_type
    ):
        return None
    return approval


def portal_reasons(
    *,
    partner_id: str,
    preapproval_id: str | None,
    activity_type: str,
    evidence: list[str],
    payee_account: str,
    document_text: list[str],
    book: ClaimBook,
) -> set[str]:
    """The findings that need nothing read off the invoice: the partner, the pre-approval, the
    activity and its evidence, the payee, and text aimed at the AI in the raw documents. assess
    uses them for every claim; the agent pipeline uses them alone when nobody could read the
    documents, so a failed read never hides them (review 37)."""
    program = book.program
    partner = book.partner(partner_id)
    reasons: set[str] = set()
    if partner is None:  # not in the book: nothing about it can be checked, so it all goes up
        reasons |= {"partner_not_approved", "payee_not_on_master"}
    elif partner.status != "approved":
        reasons.add("partner_not_approved")
    if _approval_for(partner_id, preapproval_id, activity_type, book) is None:
        reasons.add("no_preapproval")
    if activity_type not in program.eligible_activity_types:
        reasons.add("activity_ineligible")
    elif not set(program.required_evidence.get(activity_type, [])) <= set(evidence):
        reasons.add("missing_evidence")
    if partner is not None and payee_account != partner.payee_account:
        reasons.add("payee_not_on_master")
    if detect_in_texts(document_text):
        reasons.add("text_addresses_ai")
    if partner is not None and partner.ar_hold:
        reasons.add("partner_on_ar_hold")
    return reasons


def assess(claim: Claim, book: ClaimBook) -> Assessment:
    program = book.program
    partner = book.partner(claim.partner_id)
    reasons = portal_reasons(
        partner_id=claim.partner_id,
        preapproval_id=claim.preapproval_id,
        activity_type=claim.activity_type,
        evidence=claim.evidence,
        payee_account=claim.payee_account,
        document_text=claim.document_text,
        book=book,
    )
    if claim.public_sector_attendees and any(
        line.category == "hospitality" for line in claim.invoice.lines
    ):
        reasons.add("public_sector_hospitality")

    approval = _approval_for(claim.partner_id, claim.preapproval_id, claim.activity_type, book)
    if approval is not None and claim.activity_start < approval.approved_on:
        reasons.add("activity_before_approval")

    if claim.activity_end < claim.activity_start or claim.submitted_on < claim.activity_end:
        reasons.add("dates_inconsistent")
    if (claim.submitted_on - claim.activity_end).days > program.claim_window_days:
        reasons.add("claim_late")
    if partner is not None and _same(claim.invoice.issuer, partner.name):
        reasons.add("invoice_from_partner")
    if _is_duplicate(claim, book):
        reasons.add("duplicate_claim")
    if _is_split(claim, book):
        reasons.add("split_under_threshold")

    # What may be reimbursed: eligible lines, at the benchmark where a line is too far over it,
    # times the rate, capped at what was claimed, the invoice total and the pre-approved amount
    # (less what earlier claims on it have used).
    if sum((line.amount_usd for line in claim.invoice.lines), Decimal("0")) != (
        claim.invoice.total_usd
    ):
        reasons.add("lines_dont_match_invoice")
    eligible = Decimal("0")
    for line in claim.invoice.lines:
        if line.category in program.excluded_categories:
            reasons.add("excluded_cost")
            continue
        amount = line.amount_usd
        cap = program.merchandise_cap_per_unit_usd
        if line.category == "merchandise":
            if not line.units:
                reasons.add("merchandise_units_missing")  # can't be checked: a person looks
            elif amount > cap * line.units:  # the real unit price, not the stated one
                reasons.add("merchandise_over_cap")
                amount = cap * line.units
        if line.benchmark_usd is not None:
            if amount > line.benchmark_usd * (1 + program.rate_variance):
                reasons.add("rate_over_benchmark")
                amount = line.benchmark_usd
        eligible += amount
    rate = program.reimbursement_rate
    if claim.competitor_content:
        reasons.add("competitor_content")
        rate = program.competitor_reimbursement_rate
    computed = (eligible * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    if claim.claimed_usd > claim.invoice.total_usd:
        reasons.add("claimed_over_invoice")
    reimbursable = min(claim.claimed_usd, computed, claim.invoice.total_usd)
    if approval is not None:
        if reimbursable > approval.approved_usd:
            reasons.add("over_preapproval")
            reimbursable = approval.approved_usd
        used = _used_before(claim, approval, book)
        if used > 0 and reimbursable > approval.approved_usd - used:
            reasons.add("preapproval_used_up")
            reimbursable = max(approval.approved_usd - used, Decimal("0"))
    reimbursable = reimbursable.quantize(CENT, rounding=ROUND_HALF_UP)

    tier, role = route_of(reasons, claim.claimed_usd, program)
    return Assessment(
        claim_id=claim.id,
        tier=tier,
        route=ROUTES[tier],
        decided_by=role.label,
        reasons=sorted(reasons),
        reimbursable_usd=reimbursable,
        held_usd=(claim.claimed_usd - reimbursable).quantize(CENT, rounding=ROUND_HALF_UP),
    )


def route_of(
    reasons: set[str], claimed_usd: Decimal, program: Program, extra: dict | None = None
) -> tuple[Tier, Role]:
    """The tier and least senior role for a set of reasons. extra holds reasons from outside the
    rules (the agents' fixed findings), with the same (text, tier, role) shape."""
    table = {**REASONS, **(extra or {})}
    tier = max((table[r][1] for r in reasons), key=_TIER_ORDER.__getitem__, default=Tier.GREEN)
    if tier is Tier.COMPLIANCE:
        return tier, Role.COMPLIANCE_OFFICER
    # The amount at stake: whoever releases a held part needs authority for the whole claim.
    return tier, max([_ladder_role(claimed_usd, program), *(table[r][2] for r in reasons)])


def assess_all(book: ClaimBook) -> dict[str, Assessment]:
    return {claim.id: assess(claim, book) for claim in book.claims}
