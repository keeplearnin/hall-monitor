"""The adjuster approval gate: what the adjuster sees, and the rules a decision must pass.

The evidence pack is everything the AI found, in one record. It is stored under its SHA-256
(governance/audit.py), the adjuster must quote that hash back, and their signature covers it, so
the record shows which evidence the decision was made on. Decisions are checked in code: the
adjuster must be registered, the decision signed with their key (governance/signing.py) and
within their authority, the payout must fit a USD claim, paying a flagged claim needs a senior
adjuster, and changing the AI's recommendation needs a reason.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hall_monitor.claims.models import ClaimSubmission, NextStep, RiskLevel, Route, TriageResult
from hall_monitor.governance import signing
from hall_monitor.governance.audit import evidence_body, sha256
from hall_monitor.registry import ADJUSTERS_FILENAME, APPROVALS_FILENAME, DEFAULT_REGISTRY_DIR

ADJUSTERS_FILE = DEFAULT_REGISTRY_DIR / ADJUSTERS_FILENAME
MIN_REASON_LENGTH = 20


class Adjuster(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    role: str
    authority_limit_usd: float = Field(gt=0, allow_inf_nan=False)
    may_pay_flagged: bool
    public_key: str
    demo_key: bool = False


def load_adjusters(path: Path = ADJUSTERS_FILE) -> dict[str, Adjuster]:
    """The adjusters file, refused whole (ValueError, without the file's values) if it doesn't
    parse, an entry isn't valid (an authority limit must be a finite number above 0: a NaN
    limit would let any payout pass), or an ID is listed twice (the later entry could otherwise
    change someone's authority)."""
    try:
        entries = yaml.safe_load(path.read_text())["adjusters"]
        adjusters = [Adjuster.model_validate(a) for a in entries]
    except yaml.YAMLError:
        raise ValueError(f"{path.name} isn't valid YAML") from None
    except (KeyError, TypeError):
        raise ValueError(f"{path.name} has no list of adjusters") from None
    except ValidationError as exc:
        fields = ", ".join(sorted({".".join(map(str, e["loc"])) for e in exc.errors()}))
        raise ValueError(f"{path.name} has an entry that isn't valid ({fields})") from None
    seen: dict[str, Adjuster] = {}
    for adjuster in adjusters:
        if adjuster.id in seen:
            raise ValueError(f"{adjuster.id} is listed twice in {path.name}")
        seen[adjuster.id] = adjuster
    return seen


def adjusters_digest(adjusters: dict[str, Adjuster]) -> str:
    """Hash of the adjusters' canonical content, so comments and layout don't count as a
    change. Recorded in registry/approvals.yaml like the budgets' digest."""
    body = [a.model_dump(mode="json") for a in adjusters.values()]
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")))


class AdjustersNotApproved(ValueError):
    pass


def approved_adjusters(registry_dir: Path = DEFAULT_REGISTRY_DIR) -> dict[str, Adjuster]:
    """The adjusters file, if its digest is the approved one in registry/approvals.yaml;
    otherwise AdjustersNotApproved, and no decision can be validated against it. An adjusters
    or approvals file that can't be read or doesn't validate is refused the same way."""
    try:
        adjusters = load_adjusters(registry_dir / ADJUSTERS_FILENAME)
    except Exception as exc:
        raise AdjustersNotApproved(
            f"adjusters not approved: {ADJUSTERS_FILENAME} can't be read ({type(exc).__name__})"
        ) from None
    try:
        approvals = yaml.safe_load((registry_dir / APPROVALS_FILENAME).read_text()) or {}
        approved = (approvals.get("adjusters") or {}).get("digest")
    except Exception as exc:  # missing, unreadable, not YAML, or not the expected shape
        raise AdjustersNotApproved(
            f"adjusters not approved: {APPROVALS_FILENAME} can't be read ({type(exc).__name__})"
        ) from None
    if adjusters_digest(adjusters) != approved:
        raise AdjustersNotApproved(
            f"adjusters not approved: {ADJUSTERS_FILENAME} is not the approved version"
        )
    return adjusters


class AdjusterDecision(BaseModel):
    """What the adjuster submits."""

    model_config = ConfigDict(extra="forbid")

    adjuster_id: str
    next_step: NextStep
    payout_usd: float = Field(ge=0, allow_inf_nan=False)
    reason: str = ""
    evidence_sha256: str = Field(description="The hash of the evidence pack they reviewed.")
    signature: str = Field("", description="The adjuster's Ed25519 signature (base64).")

    def signed(self, claim_id: str, key: Ed25519PrivateKey) -> AdjusterDecision:
        message = signing.decision_message(claim_id, self.model_dump(mode="json"))
        return self.model_copy(update={"signature": signing.sign(message, key)})


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
    # True when the signature was made with a public demo key (registry/adjusters.yaml), so the
    # record shows the decision proves nothing about who made it.
    signed_with_demo_key: bool = False


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
            {
                "actor": t.actor,
                "tool": t.tool,
                "allowed": t.allowed,
                # Only when a result addressed the AI, so a pack built before this field
                # existed hashes the same (ADR 0035).
                **({"injection_signals": t.injection_signals} if t.injection_signals else {}),
            }
            for t in result.tool_calls
        ],
        "stopped_reason": result.stopped_reason,
        "injection_signals": result.injection_signals,
    }


def evidence_sha256(pack: dict) -> str:
    """The pack's hash: the same one the audit log stores it under."""
    return sha256(evidence_body(pack))


def ai_payout(result: TriageResult) -> float | None:
    return result.coverage.covered_amount if result.coverage else None


def changes_ai(decision: AdjusterDecision, result: TriageResult) -> bool:
    """The adjuster chose a different next step, or a different payout on a payment."""
    return decision.next_step is not result.recommendation.next_step or (
        decision.next_step is NextStep.PAY
        and abs(decision.payout_usd - (ai_payout(result) or 0)) > 0.005
    )


def validate_decision(
    decision: AdjusterDecision,
    result: TriageResult,
    pack_sha256: str,
    adjusters: dict[str, Adjuster],
) -> Adjuster:
    """Return the adjuster if the decision may stand; otherwise raise DecisionRejected."""
    adjuster = adjusters.get(decision.adjuster_id)
    if adjuster is None:
        raise DecisionRejected("the decision names an adjuster who isn't registered")
    message = signing.decision_message(result.claim_id, decision.model_dump(mode="json"))
    if not signing.verify(message, decision.signature, adjuster.public_key):
        raise DecisionRejected(f"the decision isn't signed with {adjuster.id}'s key")
    if decision.evidence_sha256 != pack_sha256:
        raise DecisionRejected("evidence hash doesn't match the current evidence pack")
    extraction = result.extraction
    in_usd = extraction is not None and extraction.currency.strip().upper() == "USD"
    # The amount claimed, in USD, or None when it can't be compared with USD payouts and limits
    # (nothing was extracted, or the claim is in another currency).
    claimed = extraction.total_claimed if in_usd else None
    senior = adjuster.may_pay_flagged
    if decision.next_step in (NextStep.PAY, NextStep.DENY) and claimed is None and not senior:
        why = f"is in {extraction.currency}" if extraction else "has no amount extracted"
        raise DecisionRejected(
            f"this claim {why}, so its amount can't be checked against USD limits; "
            "a senior adjuster must decide"
        )
    if decision.next_step is NextStep.DENY and claimed is not None:
        if claimed > adjuster.authority_limit_usd:
            raise DecisionRejected(
                f"denying {claimed:,.2f} exceeds {adjuster.id}'s authority of "
                f"{adjuster.authority_limit_usd:,.0f}; a senior adjuster must decide"
            )
    if decision.next_step is NextStep.PAY:
        if decision.payout_usd <= 0:
            raise DecisionRejected("a payment needs a payout above 0")
        if claimed is not None and decision.payout_usd > claimed + 0.005:
            raise DecisionRejected(f"payout exceeds the {claimed:,.2f} claimed")
        if decision.payout_usd > adjuster.authority_limit_usd:
            raise DecisionRejected(
                f"payout exceeds {adjuster.id}'s authority of "
                f"{adjuster.authority_limit_usd:,.0f}; a senior adjuster must decide"
            )
        if result.risk is None and not senior:
            # Stopped before the fraud check (or before intake): nothing bounds or vets this
            # payment except the person, so it needs the senior authority.
            raise DecisionRejected(
                "paying a claim the AI never assessed for fraud needs a senior adjuster"
            )
        if result.risk and (result.risk.flags or result.risk.level is not RiskLevel.LOW):
            if not senior:
                raise DecisionRejected(
                    "paying a claim with a fraud flag or above-low risk needs a senior adjuster"
                )
    elif decision.payout_usd != 0:
        raise DecisionRejected(f"{decision.next_step.value} can't carry a payout")
    if (changes_ai(decision, result) or decision.next_step is NextStep.DENY) and len(
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
    return FinalDecision(
        claim_id=result.claim_id,
        decided_by=f"adjuster:{adjuster.id}",
        adjuster_name=adjuster.name,
        next_step=decision.next_step,
        payout_usd=decision.payout_usd,
        ai_next_step=result.recommendation.next_step,
        ai_payout_usd=payout,
        overrode_ai=changes_ai(decision, result),
        reason=decision.reason,
        routing_reasons=result.routing.reasons,
        evidence_sha256=pack_sha256,
        decided_at=datetime.now(UTC).isoformat(timespec="seconds"),
        signed_with_demo_key=adjuster.demo_key,
    )


def audit_payload(final: FinalDecision, reason_hmac: str, signature: str | None = None) -> dict:
    """The final decision as the audit log keeps it: the adjuster's ID, not their name, and a
    keyed hash of the written reason, not its text (it can hold medical detail, and the log
    can't be erased). The full decision stays in the workflow's record."""
    payload = final.model_dump(mode="json", exclude={"adjuster_name", "reason"})
    payload["reason_hmac"] = reason_hmac
    if signature is not None:
        payload["signature"] = signature
    return payload


def final_auto(result: TriageResult, pack_sha256: str) -> FinalDecision:
    """A claim the routing rule let through: the AI's recommendation stands, as routed."""
    if result.routing.route is not Route.AUTO or result.coverage is None:
        raise ValueError(f"{result.claim_id} wasn't routed automatically; a person must decide")
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
