"""Remove identifiers no agent needs before claim text reaches a model.

Removed: dates of birth, email addresses, phone numbers, full card numbers (checked with the
Luhn checksum), the last digits after "card ending", passport numbers and US Social Security
numbers. Kept, because the agents need them: names, policy numbers, incident and receipt
dates, amounts and medical details (coverage depends on them; see the manifests' data
classes). Patterns are deliberately narrow: an ordinary date must never be mistaken for a
phone number, or the agents lose the evidence they decide on.

The report counts what was removed by type, never the values.
"""

from __future__ import annotations

import re
from collections import Counter

from hall_monitor.models import ClaimSubmission

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "date_of_birth",
        re.compile(
            r"\b(DOB|D\.O\.B\.|date of birth)(\s*:?\s*)"
            r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}|\d{1,2} [A-Z][a-z]+ \d{4})",
            re.IGNORECASE,
        ),
        r"\1\2[REDACTED]",
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL REDACTED]",
    ),
    (
        "passport_number",
        re.compile(r"\b(passport(?: no\.?| number)?)(\s*:?\s*)[A-Z0-9]{6,9}\b", re.IGNORECASE),
        r"\1\2[REDACTED]",
    ),
    ("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN REDACTED]"),
    (
        "card_last_digits",
        re.compile(r"\b(card ending(?: in)?\s*)\d{4}\b", re.IGNORECASE),
        r"\1[REDACTED]",
    ),
    (
        "phone",
        re.compile(
            r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w-])"
            r"|(?<![\w-])\+\d{1,3}(?:[\s-]?\d{2,4}){2,4}(?![\w-])"
        ),
        "[PHONE REDACTED]",
    ),
]

_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def redact_text(text: str, counts: Counter[str] | None = None) -> str:
    counts = counts if counts is not None else Counter()

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            counts["card_number"] += 1
            return "[CARD REDACTED]"
        return match.group(0)

    text = _CARD.sub(card, text)
    for name, pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        counts[name] += n
    return text


def redact_claim(claim: ClaimSubmission) -> tuple[ClaimSubmission, dict[str, int]]:
    """A copy of the claim with identifiers removed, and how many of each type were removed."""
    counts: Counter[str] = Counter()
    redacted = claim.model_copy(
        update={
            "narrative": redact_text(claim.narrative, counts),
            "attachments": [
                a.model_copy(update={"text": redact_text(a.text, counts)})
                for a in claim.attachments
            ],
        }
    )
    return redacted, {k: v for k, v in sorted(counts.items()) if v}
