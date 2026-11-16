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

# The system's own agents, by the names its prompts and docs use: text naming one is aimed at it.
_DESK_AGENTS = r"(?:(?:complaint )?triage (?:agent|model)|desk supervisor|supervisor agent)"
_AGENTS = (
    rf"(?:{_DESK_AGENTS}|intake agent|coverage(?: check)? agent|risk(?: and fraud)? agent|"
    r"critic agent|recommendation agent|claims supervisor|leave (?:explainer|supervisor))"
)
# Words that can only mean an AI: text addressed to one of these is aimed at the system.
_AI = (
    r"(?:ai (?:assistant|agent|model|system)|ai|a\.i\.|llm|chatbot|bot|language model|"
    rf"automated system|{_AGENTS})"
)
# Words people use of other people too (a ticket agent, a doctor's assistant): they count only
# as the first word of a line or sentence, addressed and followed by an instruction.
_ROLE = r"(?:assistant|model|agent|system)"
_START = r"(?:^|(?<=[.!?;])\s+|(?<=\n))\s*"
_ORDER = (
    r"(?:please\s+)?(?:approve|pay|ignore|route|mark|classify|set|skip|lower|raise|reduce|"
    r"downgrade|change|drop|clear|remove)\b"
)
# What the complaint triage answers, and the values an attack would tell it to give.
_URGENCY_DOWN = r"(?:low|normal|standard)"
_LEVEL = r"(?:low|medium|high)"
_CATEGORY = (
    r"(?:billing[_ ]and[_ ]fees|account[_ ]management|debt[_ ]collection[_ ]conduct|"
    r"credit[_ ]decision|payments[_ ]and[_ ]servicing|fraud[_ ]or[_ ]unauthori[sz]ed|hardship|"
    r"other)"
)
_EMPTY = r"(?:an?\s+)?(?:empty(?:\s+list)?|blank|none|nothing|\[\])"
_MUST = r"(?:must|should|shall|needs? to|has to)\s+(?:be|stay|remain)\s+(?:set\s+to\s+)?"

SIGNALS: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b"
        r"(?:previous|prior|above|earlier|all|your|system)\b[^.\n]{0,20}\b"
        r"(?:instructions?|rules|prompts?|guidelines|policy checks?)\b",
        re.IGNORECASE,
    ),
    # Addressed to an AI or to one of the system's agents, not any mention of an assistant or an
    # agent (review 38b): "the ticket agent: change fee waived" is a customer quoting a person.
    "addresses_the_ai": re.compile(
        rf"\b(?:note|message|instructions?) (?:to|for) (?:the |any )?(?:{_AI}|model|system)\b"
        rf"|\b(?:dear|attention|hey) (?:the )?(?:{_AI}|assistant|model|system)\b"
        rf"|\b{_AI}[,:]\s*{_ORDER}"
        rf"|{_START}(?:the\s+)?{_ROLE}[,:]\s*{_ORDER}"
        rf"|\bto (?:the |any )?{_AI}\s*[:,]"
        rf"|{_START}to (?:the |any )?{_ROLE}\s*:"
        rf"|\b{_DESK_AGENTS}\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    "role_play": re.compile(
        r"\byou are (?:now )?(?:a|an|the) "
        r"(?:ai|assistant|model|claims (?:agent|adjuster|handler)|"
        r"hr (?:director|manager|business partner)|leave specialist|local manager)\b"
        r"|\bact as (?:a|an|the) (?:claims )?(?:adjuster|approver|supervisor)\b"
        r"|\bact as (?:a|an|the) (?:hr (?:director|manager|business partner)|leave specialist|"
        r"local manager)\b",
        re.IGNORECASE,
    ),
    "fake_markup": re.compile(
        r"</?\s*(?:system|instructions?|reviewer_feedback|claim_submission|policy_record|"
        r"policy_wording|assistant|tool_result|previous_answer|problems|leave_request|"
        r"leave_result|submitted_at|"
        # The complaint desk's (complaints/triage_agent.py, complaints/supervisor.py).
        r"complaint|narrative|issue|sub_issue|product|sub_product|tags|received|submitted_via|"
        r"company_public_response|desk_state)\s*>"
        r"|\bsystem\s*(?:prompt|message)\s*:",
        re.IGNORECASE,
    ),
    "claims_prior_approval": re.compile(
        r"\b(?:pre-?approved|already approved|approval (?:has been )?granted)\b[^.\n]{0,40}"
        r"\b(?:by|from)\b[^.\n]{0,20}\b(?:adjuster|claims (?:team|manager|department)|"
        r"supervisor|underwriter|insurer|hr (?:manager|director|team|department|business partner)|"
        r"leave specialist)\b",
        re.IGNORECASE,
    ),
    "avoid_human_review": re.compile(
        r"\b(?:do not|don't|no need to|without)\b[^.\n]{0,30}\b"
        r"(?:send|route|escalate|refer|forward|involve|review)\b[^.\n]{0,30}\b"
        r"(?:human|person|adjuster|manual review|reviewer|leave specialist|specialist review|"
        r"specialists?|hr business partner)\b"
        r"|\b(?:keep|leave|put|route|send|move|place)\b[^.\n]{0,30}\bstandard queue\b",
        re.IGNORECASE,
    ),
    # Text telling the triage what to answer: a consumer describes the problem, not the fields.
    # A field and a value it takes, or an order to empty or lower one (review 38b: "raise my
    # concerns", "change the category of my booking" and "my priority should be low" are
    # ordinary English).
    "steers_the_triage": re.compile(
        rf"\b(?:set|lower|reduce|downgrade|change|drop|move)\s+(?:the\s+|its\s+)?urgency\s+"
        rf"(?:level\s+)?(?:to|as)\s+{_URGENCY_DOWN}\b"
        r"|\b(?:lower|reduce|downgrade)\s+(?:the\s+|its\s+)?urgency\b"
        rf"|\b(?:set|lower|reduce|raise|change|move)\s+(?:the\s+|its\s+)?confidence\s+"
        rf"(?:level\s+)?(?:to|as)\s+{_LEVEL}\b"
        rf"|\b(?:set|change|move)\s+(?:the\s+|its\s+)?category\s+(?:to|as)\s+{_CATEGORY}\b"
        rf"|\b(?:set|clear|drop|empty|remove)\s+(?:the\s+|its\s+|all\s+)?concerns\s+"
        rf"(?:list|field|array|to\s+{_EMPTY})"
        rf"|\burgency\s+(?:level\s+)?{_MUST}{_URGENCY_DOWN}\b"
        rf"|\bconfidence\s+(?:level\s+)?{_MUST}{_LEVEL}\b"
        rf"|\bcategory\s+{_MUST}{_CATEGORY}\b"
        rf"|\bconcerns?\s+(?:list\s+|field\s+)?{_MUST}{_EMPTY}",
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


def decoded(text: str) -> str:
    """The text as a reader would see it (entities, compatibility forms, look-alike letters and
    invisible characters undone), for other checks that search both forms."""
    return _normalise(text)


def _invisible(char: str) -> bool:
    """Characters that render as nothing: format characters (zero-width spaces and joiners,
    direction marks, tag characters), the combining grapheme joiner and variation selectors."""
    return (
        unicodedata.category(char) == "Cf"
        or char == "\u034f"
        or "\ufe00" <= char <= "\ufe0f"
        or "\U000e0100" <= char <= "\U000e01ef"
    )


# Letters that look like Latin ones: Cyrillic and Greek. Mapped only in the copy that is
# searched, so text in those scripts is never changed, only matched as it would read.
_CONFUSABLES = str.maketrans(
    "аеорсухіјѕԁӏкмнтвАВЕКМНОРСТХІЈЅ"  # Cyrillic
    "αεοριυνκτχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ",  # Greek
    "aeopcyxijsdlkmhtbABEKMHOPCTXIJSaeopiuvktxABEZHIKMNOPTYX",
)
_MAX_DECODES = 5  # &amp;amp;lt; is three layers; more than five is itself suspicious


def _normalise(text: str) -> str:
    """What the text says once decoded: HTML entities, decoded until stable (&lt;system&gt;,
    &amp;lt;system&amp;gt;), full-width and other compatibility forms (＜system＞), look-alike
    Cyrillic and Greek letters (sуstem) and invisible characters inside words don't hide a
    match."""
    decoded = text
    for _ in range(_MAX_DECODES):
        once = html.unescape(decoded)
        if once == decoded:
            break
        decoded = once
    decoded = unicodedata.normalize("NFKC", decoded).translate(_CONFUSABLES)
    return "".join(c for c in decoded if not _invisible(c))
