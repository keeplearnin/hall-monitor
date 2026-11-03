"""The adjuster approval gate: what the adjuster sees, and the rules a decision must pass.

The evidence pack is everything the AI found, in one record. Its SHA-256 is recorded with the
decision, and the adjuster must quote it back, so the record proves which evidence they saw.
Decisions are checked in code: the adjuster must be registered and within their authority, the
payout must fit the claim, paying a flagged claim needs a senior adjuster, and changing the AI's
recommendation needs a reason.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from hall_monitor.models import ClaimSubmission, NextStep, Route, TriageResult
from hall_monitor.registry import ADJUSTERS_FILENAME, DEFAULT_REGISTRY_DIR

ADJUSTERS_FILE = DEFAULT_REGISTRY_DIR / ADJUSTERS_FILENAME
MIN_REASON_LENGTH = 20


class Adjuster(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    role: str
    authority_limit_usd: float
    may_pay_flagged: bool


def load_adjusters(path: Path = ADJUSTERS_FILE) -> dict[str, Adjuster]:
    raw = yaml.safe_load(path.read_text())["adjusters"]
    return {a["id"]: Adjuster.model_validate(a) for a in raw}


class AdjusterDecision(BaseModel):
    """What the adjuster submits."""

    model_config = ConfigDict(extra="forbid")

    adjuster_id: str
    next_step: NextStep
    payout_usd: float = Field(ge=0)
    reason: str = ""
    evidence_sha256: str = Field(description="The hash of the evidence pack they reviewed.")


class FinalDecision(BaseModel):
    """The decision of record for a claim, by a person or by the automatic route."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    decided_by: str  # "auto" or "adjuster:<id>"
    adjuster_name: str | None
    next_step: NextStep
    payout_usd: float
    ai_next_step: NextStep
    ai_payout_usd: float | None
    overrode_ai: bool
    reason: str
    routing_reasons: list[str]
    evidence_sha256: str
    decided_at: str


class DecisionRejected(ValueError):
    pass


def build_evidence_pack(result: TriageResult, claim: ClaimSubmission) -> dict:
    """Everything the adjuster needs to decide, without reading the raw traces."""
    extraction = result.extraction
    return {
        "claim_id": result.claim_id,
        "submitted_at": claim.submitted_at.isoformat(),
        "documents": [a.filename for a in claim.attachments],
        "extraction": extraction.model_dump(mode="json") if extraction else None,
        "coverage": result.coverage.model_dump(mode="json") if result.coverage else None,
        "risk": result.risk.model_dump(mode="json") if result.risk else None,
        "recommendation": result.recommendation.model_dump(mode="json"),
        "routing_reasons": result.routing.reasons,
        "critic_changes": [
            {
                "stage": r.stage.value,
                "round": r.round,
                "changed": {f: [r.before.get(f), r.after.get(f)] for f in r.changed_fields},
            }
            for r in result.revision_history
            if r.changed_fields
        ],
        "critic_unresolved": result.critic_unresolved,
        "code_corrections": result.code_corrections,
        "records_looked_up": [
            {"actor": t.actor, "tool": t.tool, "allowed": t.allowed} for t in result.tool_calls
        ],
        "stopped_reason": result.stopped_reason,
    }


def evidence_sha256(pack: dict) -> str:
    return hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest()


def ai_payout(result: TriageResult) -> float | None:
    return result.coverage.covered_amount if result.coverage else None


def validate_decision(
    decision: AdjusterDecision,
    result: TriageResult,
    pack_sha256: str,
    adjusters: dict[str, Adjuster],
) -> Adjuster:
    """Return the adjuster if the decision may stand; otherwise raise DecisionRejected."""
    adjuster = adjusters.get(decision.adjuster_id)
    if adjuster is None:
        raise DecisionRejected(f"{decision.adjuster_id!r} is not a registered adjuster")
    if decision.evidence_sha256 != pack_sha256:
        raise DecisionRejected("evidence hash doesn't match the current evidence pack")
    claimed = result.extraction.total_claimed if result.extraction else None
    if decision.next_step is NextStep.PAY:
        if decision.payout_usd <= 0:
            raise DecisionRejected("a payment needs a payout above 0")
        if claimed is not None and decision.payout_usd > claimed + 0.005:
            raise DecisionRejected(f"payout exceeds the {claimed:,.2f} claimed")
        if decision.payout_usd > adjuster.authority_limit_usd:
            raise DecisionRejected(
                f"payout exceeds {adjuster.name}'s authority of "
                f"{adjuster.authority_limit_usd:,.0f}; a senior adjuster must decide"
            )
        if result.risk and result.risk.flags and not adjuster.may_pay_flagged:
            raise DecisionRejected("paying a claim with a fraud flag needs a senior adjuster")
    elif decision.payout_usd != 0:
        raise DecisionRejected(f"{decision.next_step.value} can't carry a payout")
    changes_ai = decision.next_step is not result.recommendation.next_step or (
        decision.next_step is NextStep.PAY
        and abs(decision.payout_usd - (ai_payout(result) or 0)) > 0.005
    )
    if (changes_ai or decision.next_step is NextStep.DENY) and len(
        decision.reason.strip()
    ) < MIN_REASON_LENGTH:
        raise DecisionRejected(
            "overriding the AI or denying a claim needs a reason of at least "
            f"{MIN_REASON_LENGTH} characters"
        )
    return adjuster


def final_from_adjuster(
    decision: AdjusterDecision, adjuster: Adjuster, result: TriageResult, pack_sha256: str
) -> FinalDecision:
    payout = ai_payout(result)
    overrode = decision.next_step is not result.recommendation.next_step or (
        decision.next_step is NextStep.PAY and abs(decision.payout_usd - (payout or 0)) > 0.005
    )
    return FinalDecision(
        claim_id=result.claim_id,
        decided_by=f"adjuster:{adjuster.id}",
        adjuster_name=adjuster.name,
        next_step=decision.next_step,
        payout_usd=decision.payout_usd,
        ai_next_step=result.recommendation.next_step,
        ai_payout_usd=payout,
        overrode_ai=overrode,
        reason=decision.reason,
        routing_reasons=result.routing.reasons,
        evidence_sha256=pack_sha256,
        decided_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def final_auto(result: TriageResult, pack_sha256: str) -> FinalDecision:
    """A claim the routing rule let through: the AI's recommendation stands, as routed."""
    assert result.routing.route is Route.AUTO and result.coverage is not None
    return FinalDecision(
        claim_id=result.claim_id,
        decided_by="auto",
        adjuster_name=None,
        next_step=result.recommendation.next_step,
        payout_usd=result.coverage.covered_amount,
        ai_next_step=result.recommendation.next_step,
        ai_payout_usd=result.coverage.covered_amount,
        overrode_ai=False,
        reason="Small, clean claim: routed automatically by the routing rule.",
        routing_reasons=[],
        evidence_sha256=pack_sha256,
        decided_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
