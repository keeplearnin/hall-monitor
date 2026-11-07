"""Flag claim text that looks like instructions aimed at the AI rather than at the insurer.

A deterministic second line of defence. The first is in the prompts: claim text is fenced and
escaped and the agents are told it is evidence, not instructions. Any signal here sends the
claim to an adjuster, whatever the agents concluded. Patterns aim at text addressed to an AI or
to the claims process itself; a customer writing "please pay my claim" doesn't match.
"""

from __future__ import annotations

import html
import re
import unicodedata

from hall_monitor.models import ClaimSubmission

_AI = r"(?:ai|a\.i\.|assistant|model|llm|chatbot|bot|agent|automated system|system)"

SIGNALS: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b"
        r"(?:previous|prior|above|earlier|all|your|system)\b[^.\n]{0,20}\b"
        r"(?:instructions?|rules|prompts?|guidelines|policy checks?)\b",
        re.IGNORECASE,
    ),
    "addresses_the_ai": re.compile(
        rf"\b(?:note|message|instructions?) (?:to|for) (?:the |any )?{_AI}\b"
        rf"|\b(?:dear|attention|hey) {_AI}\b"
        rf"|\b{_AI}[,:]\s*(?:please\s+)?(?:approve|pay|ignore|route|mark|classify|set|skip)\b",
        re.IGNORECASE,
    ),
    "role_play": re.compile(
        r"\byou are (?:now )?(?:a|an|the) "
        r"(?:ai|assistant|model|claims (?:agent|adjuster|handler)|hr (?:director|manager)|"
        r"leave specialist)\b"
        r"|\bact as (?:a|an|the) (?:claims )?(?:adjuster|approver|supervisor)\b"
        r"|\bact as (?:a|an|the) (?:hr (?:director|manager)|leave specialist)\b",
        re.IGNORECASE,
    ),
    "fake_markup": re.compile(
        r"</?\s*(?:system|instructions?|reviewer_feedback|claim_submission|policy_record|"
        r"policy_wording|assistant|tool_result|previous_answer|problems|leave_request|"
        r"leave_result|submitted_at)\s*>"
        r"|\bsystem\s*(?:prompt|message)\s*:",
        re.IGNORECASE,
    ),
    "claims_prior_approval": re.compile(
        r"\b(?:pre-?approved|already approved|approval (?:has been )?granted)\b[^.\n]{0,40}"
        r"\b(?:by|from)\b[^.\n]{0,20}\b(?:adjuster|claims (?:team|manager|department)|"
        r"supervisor|underwriter|insurer|hr (?:manager|director|team|department)|"
        r"leave specialist)\b",
        re.IGNORECASE,
    ),
    "avoid_human_review": re.compile(
        r"\b(?:do not|don't|no need to|without)\b[^.\n]{0,30}\b"
        r"(?:send|route|escalate|refer|forward|involve|review)\b[^.\n]{0,30}\b"
        r"(?:human|person|adjuster|manual review|reviewer|leave specialist)\b",
        re.IGNORECASE,
    ),
    "names_internal_tools": re.compile(
        r"\b(?:policy_lookup|claims_history|run_agent|route_claim|covered_amount|risk_flags?)\b"
    ),
}


def detect_instructions(claim: ClaimSubmission) -> list[str]:
    """Names of the signals found anywhere in the claim, in a stable order."""
    codes = [claim.channel, *(a.kind for a in claim.attachments)]
    texts = [claim.narrative, *(a.text for a in claim.attachments)]
    texts += [a.filename for a in claim.attachments]
    texts += codes + [code.replace("_", " ") for code in codes]  # tool names keep underscores
    return detect_in_texts(texts)


def detect_in_texts(texts: list[str]) -> list[str]:
    """Names of the signals found in any of the texts, in a stable order."""
    raw = "\n".join(texts)
    # The raw text and the decoded text are both searched: decoding reveals markup written as
    # entities, but it also turns &#10; into a newline, which would end a phrase early.
    decoded = _normalise(raw)
    return [name for name, p in SIGNALS.items() if p.search(raw) or p.search(decoded)]


def _invisible(char: str) -> bool:
    """Characters that render as nothing: format characters (zero-width spaces and joiners,
    direction marks, tag characters), the combining grapheme joiner and variation selectors."""
    return (
        unicodedata.category(char) == "Cf"
        or char == "\u034f"
        or "\ufe00" <= char <= "\ufe0f"
        or "\U000e0100" <= char <= "\U000e01ef"
    )


def _normalise(text: str) -> str:
    """What the text says once decoded: HTML entities (&lt;system&gt;), full-width and other
    compatibility forms (＜system＞) and invisible characters inside words don't hide a match."""
    decoded = unicodedata.normalize("NFKC", html.unescape(text))
    return "".join(c for c in decoded if not _invisible(c))
