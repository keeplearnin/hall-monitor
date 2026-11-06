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
        r"(?:ai|assistant|model|claims (?:agent|adjuster|handler))\b"
        r"|\bact as (?:a|an|the) (?:claims )?(?:adjuster|approver|supervisor)\b",
        re.IGNORECASE,
    ),
    "fake_markup": re.compile(
        r"</?\s*(?:system|instructions?|reviewer_feedback|claim_submission|policy_record|"
        r"policy_wording|assistant|tool_result|previous_answer|problems)\s*>"
        r"|\bsystem\s*(?:prompt|message)\s*:",
        re.IGNORECASE,
    ),
    "claims_prior_approval": re.compile(
        r"\b(?:pre-?approved|already approved|approval (?:has been )?granted)\b[^.\n]{0,40}"
        r"\b(?:by|from)\b[^.\n]{0,20}\b(?:adjuster|claims (?:team|manager|department)|"
        r"supervisor|underwriter|insurer)\b",
        re.IGNORECASE,
    ),
    "avoid_human_review": re.compile(
        r"\b(?:do not|don't|no need to|without)\b[^.\n]{0,30}\b"
        r"(?:send|route|escalate|refer|forward|involve|review)\b[^.\n]{0,30}\b"
        r"(?:human|person|adjuster|manual review|reviewer)\b",
        re.IGNORECASE,
    ),
    "names_internal_tools": re.compile(
        r"\b(?:policy_lookup|claims_history|run_agent|route_claim|covered_amount|risk_flags?)\b"
    ),
}


def detect_instructions(claim: ClaimSubmission) -> list[str]:
    """Names of the signals found anywhere in the claim, in a stable order."""
    texts = [claim.narrative, *(a.text for a in claim.attachments)]
    texts += [a.filename for a in claim.attachments]
    texts += [
        code.replace("_", " ") for code in (claim.channel, *(a.kind for a in claim.attachments))
    ]
    joined = _normalise("\n".join(texts))
    return [name for name, pattern in SIGNALS.items() if pattern.search(joined)]


_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))


def _normalise(text: str) -> str:
    """What the text says once decoded: HTML entities (&lt;system&gt;), full-width and other
    compatibility forms (＜system＞) and invisible characters inside words don't hide a match."""
    return unicodedata.normalize("NFKC", html.unescape(text)).translate(_INVISIBLE)
