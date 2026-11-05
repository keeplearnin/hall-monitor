"""Remove identifiers no agent needs before claim text reaches a model.

Removed: dates of birth, email addresses, phone numbers, full card numbers (checked with the
Luhn checksum), the last digits after "card ending", passport numbers, US Social Security
numbers, tax IDs (EINs, and numbers labelled TIN or tax ID), bank account numbers (when
labelled as one), routing numbers (when labelled) and IBANs (checked with their mod-97 checksum),
from the claim text, attachment file names and tool results. Kept, because the agents
need them: names, policy numbers, incident and receipt dates, amounts and medical details
(coverage depends on them; the manifests' data classes, enforced at run time, decide which
agents see them). Patterns are deliberately narrow: an ordinary date must never be mistaken for a
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
        "tin",
        re.compile(
            r"\b(TIN|EIN|ITIN|tax(?:payer)? ID|tax identification number)"
            r"(\s*(?:no\.?|number|#)?\s*:?\s*)\d{2}-?\d{7}\b",
            re.IGNORECASE,
        ),
        r"\1\2[REDACTED]",
    ),
    ("tin", re.compile(r"\b\d{2}-\d{7}\b"), "[TIN REDACTED]"),  # EIN format, unlabelled
    (
        "bank_routing",
        re.compile(
            r"\b(routing(?: number| no\.?)?|ABA(?: number| no\.?)?|RTN)(\s*:?\s*)\d{9}\b",
            re.IGNORECASE,
        ),
        r"\1\2[REDACTED]",
    ),
    (
        # Only when labelled: an unlabelled run of digits could be a receipt or booking number.
        "bank_account",
        re.compile(
            r"\b((?:bank )?(?:account|acct)\.?(?:\s*(?:number|no\.?|#))?(?:\s*ending(?: in)?)?)"
            r"(\s*:?\s*)\d(?:[ -]?\d){3,16}\b",
            re.IGNORECASE,
        ),
        r"\1\2[REDACTED]",
    ),
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
# Country code, check digits, then up to 30 letters and digits, optionally in groups of four.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")


def _iban_valid(text: str) -> bool:
    compact = text.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    moved = compact[4:] + compact[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


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

    def iban(match: re.Match[str]) -> str:
        if _iban_valid(match.group(0)):
            counts["iban"] += 1
            return "[IBAN REDACTED]"
        return match.group(0)

    # IBANs first: their digit groups could otherwise be read as a card number.
    text = _IBAN.sub(iban, text)
    text = _CARD.sub(card, text)
    for name, pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        counts[name] += n
    return text


def redact_value(value, counts: Counter[str] | None = None):
    """Redact every string inside a JSON-like value (a tool result, say)."""
    if isinstance(value, str):
        return redact_text(value, counts)
    if isinstance(value, dict):
        return {k: redact_value(v, counts) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, counts) for v in value]
    return value


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
