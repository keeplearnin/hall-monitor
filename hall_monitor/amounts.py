"""Finding amounts in claim text: shared by the routing rule and the eval scoring."""

from __future__ import annotations

import re

from hall_monitor.models import ClaimSubmission

# A dollar sign that isn't part of another currency's (C$, A$, NZ$, HK$), or USD/US$.
_USD_BEFORE = r"(?:(?<![A-Za-z])\$|\bUS\$|\bUSD)\s?"
_USD_AFTER = r"\s?USD\b"
# Not part of a date (2026-08-14, 14/08/2026), a time (21:45) or a reference (BA2026, #2026).
_NOT_BEFORE = r"(?<![\w,.:/#-])"
_NOT_AFTER = r"(?!\d|[.,]\d|[-/:]\d)"


def _pattern(amount: float, usd: bool) -> re.Pattern[str]:
    forms = {f"{amount:,.2f}", f"{amount:.2f}"}
    if float(amount).is_integer():
        forms |= {f"{int(amount):,}", f"{int(amount)}"}
    figure = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    if not usd:
        return re.compile(rf"{_NOT_BEFORE}(?P<a>{figure}){_NOT_AFTER}")
    return re.compile(
        rf"{_USD_BEFORE}(?P<a>{figure}){_NOT_AFTER}"
        rf"|{_NOT_BEFORE}(?P<b>{figure}){_NOT_AFTER}(?={_USD_AFTER})"
    )


def _spans(amount: float, text: str, usd: bool) -> list[tuple[int, int]]:
    return [
        m.span("a" if m.group("a") is not None else "b")
        for m in _pattern(amount, usd).finditer(text)
    ]


def amount_in(amount: float, text: str, *, usd: bool = False) -> bool:
    """The amount appears as a whole number in the text, with or without cents for a whole
    amount ($1,000 or 1,000.00), but never as part of another: 100 doesn't match inside 1,100,
    1,000 doesn't match 1,000.50, and 2026 doesn't match a date, a time or a reference
    (2026-08-14, BA2026). With usd, only a figure the text marks as dollars counts ($, US$ or
    USD next to it): a bare number or a figure in euros doesn't."""
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
