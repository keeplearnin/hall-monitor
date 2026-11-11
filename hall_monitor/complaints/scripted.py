"""A scripted stand-in for the triage model: for demos and offline evals, never the real thing.

It answers from the complaint's labels with fixed rules, so demo data and the CI eval can run
with no API call and no cost. It is not a measure of the model: results from it say so, and the
live eval (run only when asked, with its cost stated first) is what measures the agent.
"""

from __future__ import annotations

import html
import re
from types import SimpleNamespace

from hall_monitor.complaints.triage_agent import (
    Category,
    ComplaintTriage,
    Concern,
    Confidence,
    Urgency,
)

SCRIPTED_MODEL = "scripted-triage"

_CATEGORY_BY_PRODUCT = {
    "Debt collection": Category.DEBT_COLLECTION_CONDUCT,
    "Credit card": Category.BILLING_AND_FEES,
    "Checking or savings account": Category.ACCOUNT_MANAGEMENT,
    "Mortgage": Category.PAYMENTS_AND_SERVICING,
    "Student loan": Category.PAYMENTS_AND_SERVICING,
    "Vehicle loan or lease": Category.PAYMENTS_AND_SERVICING,
}
_CONCERNS = {
    Concern.FRAUD: re.compile(r"\bfraud\b|\bscam\b|unauthori[sz]ed", re.I),
    Concern.IDENTITY_THEFT: re.compile(r"identity theft|someone else|without my consent", re.I),
    Concern.FORECLOSURE: re.compile(r"foreclos|struggling to pay mortgage", re.I),
    Concern.DISCRIMINATION: re.compile(r"discriminat", re.I),
}
DRAFT = (
    "Thank you for your complaint. We have received it, and a member of our complaint team "
    "will review it."
)


def _field(content: str, name: str) -> str:
    match = re.search(rf"<{name}>(.*?)</{name}>", content, re.S)
    return html.unescape(match.group(1)) if match else ""


def scripted_triage(content: str) -> ComplaintTriage:
    product, issue = _field(content, "product"), _field(content, "issue")
    labels = " ".join(_field(content, n) for n in ("product", "sub_product", "issue", "sub_issue"))
    concerns = [c for c, pattern in _CONCERNS.items() if pattern.search(labels)]
    tagged = _field(content, "tags") not in ("", "none")
    category = _CATEGORY_BY_PRODUCT.get(product, Category.OTHER)
    if concerns and concerns[0] in (Concern.FRAUD, Concern.IDENTITY_THEFT):
        category = Category.FRAUD_OR_UNAUTHORISED
    elif Concern.FORECLOSURE in concerns:
        category = Category.HARDSHIP
    urgency = Urgency.HIGH if concerns or tagged else Urgency.NORMAL
    return ComplaintTriage(
        summary=f"The consumer complains about {product.lower()}: {issue.lower()}.",
        category=category,
        urgency=urgency,
        confidence=Confidence.MEDIUM,
        concerns=concerns,
        draft_acknowledgement=DRAFT,
    )


class _ScriptedMessages:
    def __init__(self):
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        content = kwargs["messages"][0]["content"]
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=scripted_triage(content),
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )


def scripted_client() -> SimpleNamespace:
    """A client whose messages.parse answers like the triage agent, from fixed rules. It marks
    itself, so the pipeline labels every answer it gives as scripted."""
    return SimpleNamespace(messages=_ScriptedMessages(), hall_monitor_scripted=True)
