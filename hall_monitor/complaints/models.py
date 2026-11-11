"""A complaint as the desk keeps it: validated, minimised, and nothing else.

CFPB's record arrives through the tool gateway already minimised (cfpb.minimise_hit): the company
name and every field outside an allow-list are dropped, and the ZIP code is cut to its first
three digits. Complaint.from_source then validates what is left strictly. A record that fails is
quarantined by the ingester with the field names and error types, never its values.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Text fields CFPB fills from fixed lists are short; a narrative, if CFPB restores them, isn't.
Label = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000)]
Narrative = Annotated[str, StringConstraints(strict=True, max_length=50_000)]


class Tag(StrEnum):
    OLDER_AMERICAN = "older_american"
    SERVICEMEMBER = "servicemember"


CFPB_TAGS = {"Older American": Tag.OLDER_AMERICAN, "Servicemember": Tag.SERVICEMEMBER}


class CfpbSource(BaseModel):
    """CFPB's _source after minimisation, validated as it arrives. Unknown fields are refused:
    the tool keeps only an allow-list, so one here means the tool and this model disagree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complaint_id: Annotated[str, StringConstraints(strict=True, pattern=r"^\d{1,12}$")]
    date_received: datetime
    date_sent_to_company: datetime | None = None
    product: Label
    sub_product: Label | None = None
    issue: Label
    sub_issue: Label | None = None
    state: Label | None = None
    zip_code: Annotated[str, StringConstraints(strict=True, pattern=r"^\d{3}XX$")] | None = None
    tags: Label | None = None
    submitted_via: Label | None = None
    company_response: Label | None = None
    company_public_response: LongText | None = None
    timely: Literal["Yes", "No"] | None = None
    complaint_what_happened: Narrative | None = None


class Complaint(BaseModel):
    """The desk's record. No company, no full ZIP code, no consumer identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complaint_id: str
    date_received: datetime
    date_sent_to_company: datetime | None
    product: str
    sub_product: str | None
    issue: str
    sub_issue: str | None
    state: str | None = Field(pattern=r"^[A-Z]{2}$")
    zip3: str | None = Field(pattern=r"^\d{3}$")
    tags: tuple[Tag, ...]
    submitted_via: str | None
    company_response: str | None
    company_public_response: str | None
    timely: bool | None
    # CFPB no longer publishes narratives (checked 2026-11-11); kept if it does again.
    narrative: str | None

    @classmethod
    def from_source(cls, source: object) -> Complaint:
        """Validate a minimised CFPB record. Raises pydantic.ValidationError or ValueError."""
        raw = CfpbSource.model_validate(source)
        tags: list[Tag] = []
        for part in (raw.tags or "").split(","):
            name = part.strip()
            if not name:
                continue
            if name not in CFPB_TAGS:
                raise ValueError("unknown tag")
            tags.append(CFPB_TAGS[name])
        return cls(
            complaint_id=raw.complaint_id,
            date_received=raw.date_received,
            date_sent_to_company=raw.date_sent_to_company,
            product=raw.product,
            sub_product=raw.sub_product,
            issue=raw.issue,
            sub_issue=raw.sub_issue,
            # Military and territory codes are two letters too; anything else isn't kept.
            state=raw.state if raw.state and re.fullmatch(r"[A-Z]{2}", raw.state) else None,
            zip3=raw.zip_code[:3] if raw.zip_code else None,
            tags=tuple(sorted(set(tags))),
            submitted_via=raw.submitted_via,
            company_response=raw.company_response,
            company_public_response=raw.company_public_response,
            timely=None if raw.timely is None else raw.timely == "Yes",
            narrative=raw.complaint_what_happened or None,
        )

    def content_sha256(self) -> str:
        """Hash of the record's content, to tell an update from a repeat."""
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()
