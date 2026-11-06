"""Remove identifiers no agent needs before claim text reaches a model.

Removed: dates of birth, email addresses, phone numbers, full card numbers (checked with the
Luhn checksum), the last digits after "card ending", passport numbers, US Social Security
numbers, IBANs (checked with their mod-97 checksum), and tax IDs, bank account numbers and
routing numbers when labelled as such, from the claim text, attachment file names and tool
results. Kept, because the agents
need them: names, policy numbers, incident and receipt dates, amounts and medical details
(coverage depends on them; the manifests' data classes, enforced at run time, decide which
agents see them). Patterns are deliberately narrow: an ordinary date must never be mistaken for a
phone number, or the agents lose the evidence they decide on.

The report counts what was removed by type, never the values. A value two patterns could match
is counted under the first (an ITIN in SSN format counts as an SSN).
"""

from __future__ import annotations

import re
from collections import Counter

from hall_monitor.models import ClaimSubmission

# Between a label and its number: punctuation, a "no."/"number"/"#", an "is". Every run is
# bounded, so a label followed by a long blank stretch of OCR text can't make matching slow.
_SEP = r"[\s:#.]{0,6}(?:(?:no\.?|number|num\.?)[\s:#.]{0,6})?(?:is[\s:]{1,4})?"

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
            r"\b(TIN|EIN|ITIN|tax(?:payer)? ID|tax identification number)(" + _SEP + r")"
            r"\d{2}[- ]?\d{7}\b",
            re.IGNORECASE,
        ),
        r"\1\2[REDACTED]",
    ),
    (
        "bank_routing",
        re.compile(r"\b(routing|ABA|RTN)(" + _SEP + r")\d{3}[- ]?\d{3}[- ]?\d{3}\b", re.IGNORECASE),
        r"\1\2[REDACTED]",
    ),
    (
        # The last four digits after "account ending": nothing more, so an amount after stays.
        "bank_account",
        re.compile(
            r"\b((?:bank )?(?:account|acct|acc|a/c)\.?[ \t]{0,4}ending(?: in)?[ \t:]{0,4})"
            r"\d{4}\b",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
    (
        # Only after "account number", "acct #", "A/C no." and the like: "account" alone, or an
        # unlabelled run of digits, could be an amount, a date, a receipt or a booking number.
        "bank_account",
        re.compile(
            r"\b((?:bank )?(?:account|acct|acc|a/c)\.?[\s.]{0,4}(?:number|num\.?|no\.?|#)"
            r"[\s:#.]{0,6}(?:is[\s:]{1,4})?)"
            r"\d(?:[ -]?\d){3,16}\b(?![.,]\d)",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
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
# Country code, check digits, then up to 30 letters and digits, optionally in groups of four
# split by spaces or dashes. Upper case anywhere; any case right after an "IBAN" label.
_IBAN_SHAPE = r"[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){2,7}(?:[ -]?[A-Z0-9]{1,3})?"
_IBAN = re.compile(r"\b" + _IBAN_SHAPE + r"\b")
_IBAN_LABELLED = re.compile(r"(?i)\b(IBAN[\s:#.]{0,6})(" + _IBAN_SHAPE + r")\b")


def _iban_valid(text: str) -> bool:
    compact = re.sub(r"[ -]", "", text).upper()
    if not 15 <= len(compact) <= 34:
        return False
    moved = compact[4:] + compact[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def _iban_length(text: str) -> int | None:
    """Length of the longest valid IBAN at the start of text, cut at a separator or the end:
    the shape can run on into a word after it ("... 7034 EUR")."""
    cuts = [m.start() for m in re.finditer(r"[ -]", text)] + [len(text)]
    for cut in reversed(cuts):
        if _iban_valid(text[:cut]):
            return cut
    return None


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
        label, value = (
            (match.group(1), match.group(2)) if match.re is _IBAN_LABELLED else ("", match.group(0))
        )
        cut = _iban_length(value)
        if cut is None:
            return match.group(0)
        counts["iban"] += 1
        return f"{label}[IBAN REDACTED]{value[cut:]}"

    # IBANs first: their digit groups could otherwise be read as a card number.
    text = _IBAN_LABELLED.sub(iban, text)
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
