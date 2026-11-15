"""Typed records for the partner claims example: program terms, partners, claims, answer keys.

Money is Decimal throughout (read from strings in the YAML), so a boundary like "exactly 10% over
benchmark" stays exact.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LadderStep(_Strict):
    role: str
    up_to_usd: Decimal | None  # None: no upper limit


class SplitRule(_Strict):
    window_days: int = Field(gt=0)
    band: Decimal = Field(gt=0, le=1)
    min_claims: int = Field(ge=2)


class Program(_Strict):
    vendor: str
    eligible_activity_types: list[str]
    required_evidence: dict[str, list[str]]
    claim_window_days: int = Field(gt=0)
    reimbursement_rate: Decimal = Field(gt=0, le=1)
    competitor_reimbursement_rate: Decimal = Field(ge=0, le=1)
    merchandise_cap_per_unit_usd: Decimal = Field(gt=0)
    excluded_categories: list[str]
    rate_variance: Decimal = Field(ge=0)
    duplicate_fields_to_match: int = Field(ge=1, le=5)
    split: SplitRule
    approval_ladder: list[LadderStep]
    retention_months: int = Field(gt=0)


class Partner(_Strict):
    id: str
    name: str
    status: Literal["approved", "suspended", "not_approved"]
    ar_hold: bool
    payee_account: str


class PreApproval(_Strict):
    id: str
    partner_id: str
    activity_type: str
    approved_on: dt.date
    approved_usd: Decimal = Field(ge=0)


class InvoiceLine(_Strict):
    category: str
    description: str
    amount_usd: Decimal = Field(ge=0)
    units: int | None = Field(default=None, gt=0)
    unit_price_usd: Decimal | None = Field(default=None, ge=0)
    benchmark_usd: Decimal | None = Field(default=None, gt=0)


class Invoice(_Strict):
    number: str
    issuer: str
    total_usd: Decimal = Field(ge=0)
    lines: list[InvoiceLine] = []


class Claim(_Strict):
    id: str
    partner_id: str
    preapproval_id: str | None
    activity_type: str
    activity_start: dt.date
    activity_end: dt.date
    submitted_on: dt.date
    claimed_usd: Decimal = Field(ge=0)
    invoice: Invoice
    evidence: list[str]
    competitor_content: bool = False
    public_sector_attendees: bool = False
    payee_account: str
    # Text found in the claim's documents (an extraction agent's output in slice 2): checked for
    # text aimed at the AI, never followed.
    document_text: list[str] = []


class PaidClaim(_Strict):
    """An earlier claim, kept to catch duplicates."""

    id: str
    partner_id: str
    status: Literal["paid", "approved", "open"]
    invoice: Invoice
    activity_start: dt.date
    activity_end: dt.date


class Answer(_Strict):
    """A hand-written answer key for one claim."""

    tier: Literal["green", "yellow", "red", "compliance"]
    route: str
    decided_by: str
    reasons: list[str]
    reimbursable_usd: Decimal
    held_usd: Decimal
