"""Draft acknowledgements: written for a person to read, kept inside the system, never sent.

A DraftResponse has no field that could hold an address, a recipient, a channel or a send time,
and never_sent can only be True. It is stored in the audit log's evidence store by its hash; the
log entry records that hash (as evidence_sha256, so the log's verify checks the stored draft
hasn't been changed), never_sent and delivery "none", never the text. No code reads a draft for
delivery; the static check (hall_monitor/no_send.py) is a tripwire for adding a way to send one,
not a proof that none can be written.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hall_monitor.governance.audit import AuditLog

MAX_DRAFT_CHARS = 2000


class DraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    complaint_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=MAX_DRAFT_CHARS)
    never_sent: Literal[True] = True
    written_by: str = Field(min_length=1, description="The agent ID that wrote it.")
    prompt_version: str = Field(min_length=1)


def store_draft(audit: AuditLog, draft: DraftResponse) -> str:
    """Keep the draft in the evidence store and log it as never sent; return its hash."""
    digest = audit.put_evidence({"kind": "draft_response", **draft.model_dump(mode="json")})
    audit.append(
        draft.complaint_id,
        "draft_stored",
        draft.written_by,
        {
            "evidence_sha256": digest,
            "never_sent": True,
            "delivery": "none",
            "written_by": draft.written_by,
            "prompt_version": draft.prompt_version,
        },
    )
    return digest
