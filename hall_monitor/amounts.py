"""Finding amounts in claim text: shared by the routing rule and the eval scoring."""

from __future__ import annotations

import re

from hall_monitor.models import ClaimSubmission

# A dollar sign that isn't part of another currency's (C$, A$, NZ$, HK$), or USD/US$, with at
# most one space or tab before the figure: a figure on the next line isn't marked.
_USD_BEFORE = r"(?:(?<![A-Za-z])\$|\bUS\$|\bUSD)[ \t]?"
_USD_AFTER = r"[ \t]?USD\b"
# Not part of a date (2026-08-14, 14/08/2026), a time (21:45) or a reference (BA2026, #2026).
_NOT_BEFORE = r"(?<![\w,.:/#-])"
_NOT_AFTER = r"(?!\d|[.,]\d|[-/:]\d)"
# Another currency written next to the dollar mark or the figure (CAD $450, HK $450, (cad) $450,
# $450 CAD, €450 USD), or a minus sign (a discount or refund line). Any three capitals other
# than USD count, and so do the dollar and peso currencies' codes and prefixes in any case.
_OTHER_CODES = (
    "aud|cad|nzd|hkd|sgd|twd|mxn|ars|clp|cop|brl|php|uyu|cuc|jmd|ttd|bsd|bbd|bzd|fjd|lrd|nad|"
    "srd|xcd|kyd|bmd|gyd|sbd|zwd|hk|nz|au|ca|cdn|aus|sg|nt|mx|ar|cl|co|uy"
)
_SEP = r"[ \t:()\[\]]{0,3}"  # spaces, a colon or brackets between a code and the mark
_MINUS = r"[-\u2212\u2013\u2014][ \t]?"  # hyphen, minus sign, en and em dash
_OTHER_BEFORE = re.compile(
    rf"(?:\b(?:(?!USD)[A-Z]{{3}}|(?i:{_OTHER_CODES})){_SEP}|[€£¥]{_SEP}|{_MINUS})$"
)
_OTHER_AFTER = re.compile(rf"^{_SEP}(?:(?:(?!USD\b)[A-Z]{{3}}|(?i:{_OTHER_CODES}))\b|[€£¥])")


def _pattern(amount: float, usd: bool) -> re.Pattern[str]:
    forms = {f"{amount:,.2f}", f"{amount:.2f}"}
    if float(amount).is_integer():
        forms |= {f"{int(amount):,}", f"{int(amount)}"}
    figure = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    if not usd:
        return re.compile(rf"{_NOT_BEFORE}(?P<a>{figure}){_NOT_AFTER}")
    return re.compile(
        rf"(?P<m>{_USD_BEFORE})(?P<a>{figure}){_NOT_AFTER}"
        rf"|{_NOT_BEFORE}(?P<b>{figure}){_NOT_AFTER}(?={_USD_AFTER})"
    )


def _spans(amount: float, text: str, usd: bool) -> list[tuple[int, int]]:
    spans = []
    for m in _pattern(amount, usd).finditer(text):
        group = "a" if m.group("a") is not None else "b"
        start, end = m.span(group)
        if usd:
            marked_from = m.start("m") if group == "a" else start
            after = end if group == "a" else m.end()
            if _OTHER_BEFORE.search(text[max(0, marked_from - 8) : marked_from]):
                continue
            if _OTHER_AFTER.search(text[after : after + 8]):
                continue
        spans.append((start, end))
    return spans


def amount_in(amount: float, text: str, *, usd: bool = False) -> bool:
    """The amount appears as a whole number in the text, with or without cents for a whole
    amount ($1,000 or 1,000.00), but never as part of another: 100 doesn't match inside 1,100,
    1,000 doesn't match 1,000.50, and 2026 doesn't match a date, a time or a reference
    (2026-08-14, BA2026). With usd, only a figure the text marks as US dollars counts ($, US$ or
    USD next to it, on the same line) and nothing names another currency beside it: a bare
    number, EUR 450, CAD $450, $450 CAD or -$20 doesn't. An unrelated capitalised three-letter
    word beside the figure ($45.00 ATM fee) doesn't count either; that claim goes to a person."""
    return bool(_spans(amount, text, usd))


def untraced(amounts: list[float], text: str, *, usd: bool = False) -> list[float]:
    """The amounts that don't each have their own place in the text. Every amount uses up the
    occurrence it matched, so an item listed twice needs to appear twice."""
    used: set[tuple[int, int]] = set()
    missing = []
    for amount in amounts:
        span = next((s for s in _spans(amount, text, usd) if s not in used), None)
        if span is None:
            missing.append(amount)
        else:
            used.add(span)
    return missing


def source_text(claim: ClaimSubmission) -> str:
    """The narrative and every attachment: all the claimant sent."""
    return "\n".join([claim.narrative, *(a.text for a in claim.attachments)])
