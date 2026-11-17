"""What code checks in a claim before any agent reads it: identifiers removed, and text that
looks like instructions to the AI flagged.

The patterns are shared (hall_monitor.gateway.redaction and hall_monitor.gateway.injection);
which fields of a claim they run over is claims code.
"""

from __future__ import annotations

from collections import Counter

from hall_monitor.claims.models import ClaimSubmission
from hall_monitor.gateway.injection import detect_in_texts
from hall_monitor.gateway.redaction import redact_text


def detect_instructions(claim: ClaimSubmission) -> list[str]:
    """Names of the signals found anywhere in the claim, in a stable order."""
    codes = [claim.channel, *(a.kind for a in claim.attachments)]
    texts = [claim.narrative, *(a.text for a in claim.attachments)]
    texts += [a.filename for a in claim.attachments]
    texts += codes + [code.replace("_", " ") for code in codes]  # tool names keep underscores
    return detect_in_texts(texts)


def redact_claim(claim: ClaimSubmission) -> tuple[ClaimSubmission, dict[str, int]]:
    """A copy of the claim with identifiers removed, and how many of each type were removed."""
    counts: Counter[str] = Counter()
    redacted = claim.model_copy(
        update={
            "narrative": redact_text(claim.narrative, counts),
            "attachments": [
                a.model_copy(
                    update={
                        "filename": redact_text(a.filename, counts),
                        "text": redact_text(a.text, counts),
                    }
                )
                for a in claim.attachments
            ],
        }
    )
    return redacted, {k: v for k, v in sorted(counts.items()) if v}
