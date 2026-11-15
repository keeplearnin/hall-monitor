"""Finding amounts in claim text: shared by the routing rule and the eval scoring."""

from __future__ import annotations

import re

from hall_monitor.models import ClaimSubmission

# Any space but a line break: a figure on the next line isn't marked, but a non-breaking space
# (common in text taken from PDFs) or a run of spaces is still a space.
_SP = r"[^\S\n]"
# A dollar sign that isn't part of another currency's (C$, A$, NZ$, HK$), or USD/US$, with at
# most one space before the figure.
_USD_BEFORE = rf"(?:(?<![A-Za-z])\$|\bUS\$|\bUSD){_SP}?"
_USD_AFTER = rf"{_SP}?USD\b"
# Only USD or US$: used when the documents name another currency, where a bare "$" may be it.
_USD_ONLY_BEFORE = rf"(?:\bUS\$|\bUSD){_SP}?"
# Not part of a date (2026-08-14, 14/08/2026), a time (21:45) or a reference (BA2026, #2026).
_NOT_BEFORE = r"(?<![\w,.:/#-])"
_NOT_AFTER = r"(?!\d|[.,]\d|[-/:]\d)"
# Another currency named next to the dollar mark or the figure (CAD $450, HK $450, Cdn. $450,
# $450 CAD, $450 in CAD, €450 USD), or a minus sign touching the mark (a refund line). This is a
# list of common spellings, a backstop behind the intake's currency label and the rule that a
# non-USD claim needs a senior adjuster; it doesn't parse currency. Any three capitals other than
# USD count; codes and names below count in any case. Two-letter forms that are also English
# words or US state codes (CA, CO, AR, AU) are left out, so ordinary US receipts aren't refused.
_OTHER_CODES = (
    "aud|cad|nzd|hkd|sgd|twd|mxn|ars|clp|cop|brl|php|uyu|jmd|ttd|bsd|bbd|bzd|fjd|lrd|nad|srd|"
    "xcd|kyd|bmd|gyd|sbd|cdn|aus|hk|nz|sg|nt|mx|"
    r"canadian|australian|mexican|singapore|hong\s?kong|new\s?zealand|pesos?|euros?"
)
_SEP = rf"(?:{_SP}|[:()\[\].,=]){{0,4}}"  # spaces and punctuation between a code and the mark
_CODE = rf"(?:(?!USD\b)[A-Z]{{3}}|(?i:{_OTHER_CODES}))\b"
_OTHER_BEFORE = re.compile(rf"(?:\b{_CODE}{_SEP}|[€£¥]{_SEP}|[-\u2212\u2013\u2014])$")
_OTHER_AFTER = re.compile(rf"^{_SEP}(?:(?i:in){_SP}+{_SEP})?(?:{_CODE}|[€£¥])")
# Accounting negatives, refunds like the minus sign: the marked figure in parentheses, ($200.00)
# or (USD 200.00), or followed by CR ($150.00 CR).
_OPEN_PAREN = re.compile(rf"\({_SP}*$")
_CLOSE_PAREN = re.compile(rf"^{_SP}*\)")
_CREDIT_AFTER = re.compile(rf"^{_SP}*\)?{_SP}*(?i:cr)\b")


def _pattern(amount: float, usd: bool, strict: bool = False) -> re.Pattern[str]:
    forms = {f"{amount:,.2f}", f"{amount:.2f}"}
    if float(amount).is_integer():
        forms |= {f"{int(amount):,}", f"{int(amount)}"}
    figure = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    if not usd:
        return re.compile(rf"{_NOT_BEFORE}(?P<a>{figure}){_NOT_AFTER}")
    return re.compile(
        rf"(?P<m>{_USD_ONLY_BEFORE if strict else _USD_BEFORE})(?P<a>{figure}){_NOT_AFTER}"
        rf"|{_NOT_BEFORE}(?P<b>{figure}){_NOT_AFTER}(?={_USD_AFTER})"
    )


def _spans(amount: float, text: str, usd: bool, strict: bool = False) -> list[tuple[int, int]]:
    spans = []
    for m in _pattern(amount, usd, strict).finditer(text):
        group = "a" if m.group("a") is not None else "b"
        start, end = m.span(group)
        if usd:
            marked_from = m.start("m") if group == "a" else start
            after = end if group == "a" else m.end()
            if _OTHER_BEFORE.search(text[max(0, marked_from - 24) : marked_from]):
                continue
            if _OTHER_AFTER.search(text[after : after + 24]):
                continue
            if group == "b":  # past the USD after the figure
                after += re.match(_USD_AFTER, text[after:]).end()
            tail = text[after : after + 24]
            if _CREDIT_AFTER.search(tail) or (
                _OPEN_PAREN.search(text[max(0, marked_from - 24) : marked_from])
                and _CLOSE_PAREN.search(tail)
            ):
                continue
        spans.append((start, end))
    return spans


def amount_in(amount: float, text: str, *, usd: bool = False, strict: bool = False) -> bool:
    """The amount appears as a whole number in the text, with or without cents for a whole
    amount ($1,000 or 1,000.00), but never as part of another: 100 doesn't match inside 1,100,
    1,000 doesn't match 1,000.50, and 2026 doesn't match a date, a time or a reference
    (2026-08-14, BA2026). With usd, only a figure the text marks as US dollars counts ($, US$ or
    USD next to it, on the same line) and nothing names another currency beside it: a bare
    number, EUR 450, CAD $450, Cdn. $450, $450 in CAD or a refund (-$20, ($20.00), $20.00 CR)
    doesn't. An unrelated capitalised
    three-letter word beside the figure ($45.00 ATM fee) doesn't count either; that claim goes to
    a person. The other-currency list is in _OTHER_CODES; see HM-04's gaps for what it misses."""
    return bool(_spans(amount, text, usd, strict))


def untraced(
    amounts: list[float], text: str, *, usd: bool = False, strict: bool = False
) -> list[float]:
    """The amounts that don't each have their own place in the text. Every amount uses up the
    occurrence it matched, so an item listed twice needs to appear twice."""
    used: set[tuple[int, int]] = set()
    missing = []
    for amount in amounts:
        span = next((s for s in _spans(amount, text, usd, strict) if s not in used), None)
        if span is None:
            missing.append(amount)
        else:
            used.add(span)
    return missing


# A currency named anywhere in the documents, not only beside a figure: a header saying "all
# amounts in CAD", a prefixed dollar (C$120), a symbol (€, £, ¥, ₹) or a currency's name. When
# the documents name one, a bare "$" may be that currency, so the routing rule then counts only
# figures marked USD or US$ (strict); "Total: USD 38.50 (converted from JPY)" still traces.
# Codes are a fixed list of common currencies, upper case only,
# leaving out codes that are also English words (ALL, TOP, CUP, TRY); names count in any case.
_NAMED_CURRENCIES: dict[str, str] = {
    **{
        code: code
        for code in (
            "EUR GBP CAD AUD NZD JPY CNY RMB CHF HKD SGD MXN INR BRL ZAR SEK NOK DKK KRW THB PHP "
            "IDR MYR TWD AED SAR ILS PLN CZK HUF ARS CLP COP PEN"
        ).split()
    },
    "RMB": "CNY",
}
_CURRENCY_CODES = re.compile(r"(?<![A-Za-z])(" + "|".join(_NAMED_CURRENCIES) + r")(?![A-Za-z])")
_CURRENCY_SIGNS = {
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
    "₱": "PHP",
    "₪": "ILS",
}
_PREFIXED_DOLLARS = re.compile(r"(?<![A-Za-z])(C|CA|A|AU|NZ|HK|S|NT|MX|R)\$")
_PREFIX_CODES = {
    "C": "CAD",
    "CA": "CAD",
    "A": "AUD",
    "AU": "AUD",
    "NZ": "NZD",
    "HK": "HKD",
    "S": "SGD",
    "NT": "TWD",
    "MX": "MXN",
    "R": "BRL",
}
_CURRENCY_NAMES = [
    (re.compile(rf"(?i)\b{name}\b"), code)
    for name, code in (
        (r"euros?", "EUR"),
        (r"pounds?\s+sterling|sterling|british\s+pounds?", "GBP"),
        (r"canadian\s+dollars?|loonies?", "CAD"),
        (r"australian\s+dollars?", "AUD"),
        (r"new\s+zealand\s+dollars?", "NZD"),
        (r"hong\s+kong\s+dollars?", "HKD"),
        (r"singapore\s+dollars?", "SGD"),
        (r"mexican\s+pesos?|pesos?", "MXN"),
        (r"(?:japanese\s+)?yen", "JPY"),
        (r"yuan|renminbi", "CNY"),
        (r"swiss\s+francs?|francs?", "CHF"),
        (r"rupees?", "INR"),
        (r"reais|real\s+brasileiro", "BRL"),
        (r"rand", "ZAR"),
        (r"kron(?:a|or|e|er)", "SEK"),
        (r"won", "KRW"),
        (r"baht", "THB"),
        (r"dirhams?", "AED"),
        (r"zloty", "PLN"),
        (r"shekels?", "ILS"),
    )
]


def other_currencies(text: str) -> list[str]:
    """Every currency other than the US dollar the text names anywhere, as codes, sorted. "won"
    and "rand" are names too, so a document using them as words goes to a person; that errs
    towards review."""
    found = {_NAMED_CURRENCIES[m.group(1)] for m in _CURRENCY_CODES.finditer(text)}
    found |= {code for sign, code in _CURRENCY_SIGNS.items() if sign in text}
    found |= {_PREFIX_CODES[m.group(1)] for m in _PREFIXED_DOLLARS.finditer(text)}
    found |= {code for pattern, code in _CURRENCY_NAMES if pattern.search(text)}
    return sorted(found)


def source_text(claim: ClaimSubmission) -> str:
    """The narrative and every attachment: all the claimant sent."""
    return "\n".join([claim.narrative, *(a.text for a in claim.attachments)])
