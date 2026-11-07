"""The HR sign-off: a person decides each leave request, signed, within their role.

Every prepared request (hr/pipeline.py) leaves an evidence pack in the audit database, stored
once under its hash: the structured request, the code's checks and route, and the explanation.
A decision counts only if the staff member is registered (registry/hr_staff.yaml), it is signed
with their key over the request, every decision field and that evidence hash
(governance/signing.py), and code finds it within their role:

- their role is at least the one the routing rule named for the request;
- approving FMLA leave needs an eligible employee, a qualifying reason, and no more weeks than
  are left or qualify; approving fewer than were asked is a partial denial, so it needs an HR
  manager or above and a reason;
- denying needs an HR manager or above, and a reason;
- extending needs an HR director, a reason, an eligible employee with a qualifying reason, and
  a request past the entitlement, for no more weeks than were asked;
- approving a request the rule sent to HR review needs a reason;
- asking for more information is open to anyone registered, and isn't a decision: the request
  keeps waiting.

The audit log, not any other file, says whether a request was decided: one final decision per
request, refused if there is one already. Every refused decision on a prepared request is
logged with why. What the log keeps of a decision is impersonal: the
staff ID and role, the outcome, the weeks, and keyed hashes of the reason and of the signed
decision. The signed decision itself (with its reason and signature) is stored beside the
evidence pack, in the store that can be erased, not in the chain: a signature in the chain
could confirm a guessed reason to anyone with the public key.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field

from hall_monitor.governance import signing
from hall_monitor.governance.audit import (
    AlreadyRecorded,
    AuditLog,
    EvidenceChanged,
    canonical,
    evidence_body,
    sha256,
)
from hall_monitor.hr.routing import Role
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, HR_STAFF_FILENAME

MIN_REASON_LENGTH = 20


class StaffMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    role: str
    public_key: str
    demo_key: bool = False

    @property
    def rank(self) -> Role:
        return Role[self.role.upper()]


def load_staff(path: Path = DEFAULT_REGISTRY_DIR / HR_STAFF_FILENAME) -> dict[str, StaffMember]:
    """The staff file, refused whole if any entry names a role the rules don't know or an ID
    twice (the later entry could otherwise change someone's role)."""
    members = [StaffMember.model_validate(m) for m in yaml.safe_load(path.read_text())["staff"]]
    seen: dict[str, StaffMember] = {}
    for member in members:
        if member.role.upper() not in Role.__members__:
            raise ValueError(f"{member.id}: role {member.role!r} isn't one the rules know")
        if member.id in seen:
            raise ValueError(f"{member.id} is listed twice in {path.name}")
        seen[member.id] = member
    return seen


class Outcome(StrEnum):
    APPROVE = "approve"  # FMLA leave, within the entitlement
    DENY = "deny"
    REQUEST_INFORMATION = "request_information"
    EXTEND = "extend"  # leave past the entitlement, as an accommodation


class LeaveDecision(BaseModel):
    """What the staff member submits."""

    model_config = ConfigDict(extra="forbid")

    staff_id: str
    outcome: Outcome
    weeks: float = Field(0, ge=0, le=52, allow_inf_nan=False)  # approved or extended weeks
    reason: str = ""
    evidence_sha256: str = Field(description="The hash of the evidence pack they reviewed.")
    signature: str = Field("", description="Their Ed25519 signature (base64).")

    def signed(self, request_id: str, key: Ed25519PrivateKey) -> LeaveDecision:
        message = signing.decision_message(request_id, self.model_dump(mode="json"))
        return self.model_copy(update={"signature": signing.sign(message, key)})


class DecisionRefused(ValueError):
    pass


# --- The evidence pack -----------------------------------------------------------------------


def evidence_pack(prepared) -> dict:
    """What the person decides on: the redacted message, the structured request, the code's
    result and the note, and a random nonce, so the pack's hash can't be confirmed by guessing a
    pack with few values."""
    return {
        "request_id": prepared.request_id,
        "employee_id": prepared.employee_id,
        "request": None if prepared.request is None else prepared.request.model_dump(mode="json"),
        "assessment": (
            None if prepared.assessment is None else prepared.assessment.model_dump(mode="json")
        ),
        "route": prepared.route.model_dump(),
        "explanation": (
            None if prepared.explanation is None else prepared.explanation.model_dump()
        ),
        "explanation_problem": prepared.explanation_problem,
        "injection_signals": prepared.injection_signals,
        "stopped_reason": prepared.stopped_reason,
        # What the employee wrote, identifiers redacted: the person checks the extraction
        # against it. Erasable with the pack; never in the log.
        "message": prepared.message_redacted,
        "nonce": secrets.token_hex(16),
    }


def record_evidence(audit: AuditLog, prepared) -> str:
    """Store the pack and log its hash, once per request: a second preparation of the same
    request is refused, and the first pack stands."""
    digest = audit.put_evidence(evidence_pack(prepared))
    try:
        audit.append(
            prepared.request_id,
            "leave_evidence",
            "pipeline",
            {"evidence_sha256": digest},
            once=True,
        )
    except AlreadyRecorded:
        raise ValueError(
            f"{prepared.request_id} was already prepared; the first evidence pack stands"
        ) from None
    return digest


def evidence(audit: AuditLog, request_id: str) -> tuple[str, dict] | None:
    """The pack the log recorded for a request, and its hash, or None."""
    entries = [e for e in audit.entries(request_id) if e["event"] == "leave_evidence"]
    if not entries:
        return None
    digest = entries[0]["payload"]["evidence_sha256"]
    pack = audit.get_evidence(digest)  # raises EvidenceChanged if it no longer matches
    if pack is None:
        raise DecisionRefused("the evidence pack recorded for this request is no longer stored")
    return digest, pack


# --- The decision ----------------------------------------------------------------------------


def _check(pack: dict) -> dict[str, bool | None]:
    checks = (pack.get("assessment") or {}).get("checks") or []
    return {c["id"]: c["passed"] for c in checks}


def validate(
    decision: LeaveDecision,
    request_id: str,
    pack_sha256: str,
    pack: dict,
    staff: dict[str, StaffMember],
) -> StaffMember:
    """The staff member, if the decision passes every rule; DecisionRefused otherwise."""
    member = staff.get(decision.staff_id)
    if member is None:
        raise DecisionRefused("the decision names someone who isn't registered HR staff")
    message = signing.decision_message(request_id, decision.model_dump(mode="json"))
    if not signing.verify(message, decision.signature, member.public_key):
        raise DecisionRefused(f"the decision isn't signed with {member.id}'s key")
    if decision.evidence_sha256 != pack_sha256:
        raise DecisionRefused("evidence hash doesn't match the evidence pack for this request")
    outcome, weeks, reason = decision.outcome, decision.weeks, decision.reason.strip()
    if outcome is Outcome.REQUEST_INFORMATION:
        if weeks:
            raise DecisionRefused("asking for more information can't carry weeks")
        return member  # not a decision: the request keeps waiting
    route = pack["route"]
    needed = Role[route["decided_by"].upper()]
    if member.rank < needed:
        raise DecisionRefused(
            f"this request needs a {needed.label} or above; {member.id} is a {member.role}"
        )
    checks = _check(pack)
    assessment = pack.get("assessment") or {}
    remaining = assessment.get("remaining_weeks")
    requested = assessment.get("requested_weeks")
    qualifying = assessment.get("qualifying_weeks", requested)
    person = [checks.get(c) for c in ("employer", "service", "hours", "worksite")]
    eligible = all(p is True for p in person) and checks.get("reason") is True

    def needs_reason(what: str) -> None:
        if len(reason) < MIN_REASON_LENGTH:
            raise DecisionRefused(
                f"{what} needs a reason of at least {MIN_REASON_LENGTH} characters"
            )

    def needs_manager(what: str) -> None:
        if member.rank < Role.HR_MANAGER:
            raise DecisionRefused(f"{what} needs an HR manager or above")

    if outcome is Outcome.APPROVE:
        if not eligible:
            raise DecisionRefused(
                "FMLA leave can be approved only for an eligible employee with a qualifying "
                "reason; a person decides anything else outside this step"
            )
        if remaining is None or weeks <= 0 or weeks > remaining + 1e-9:
            raise DecisionRefused("approved weeks must be more than 0 and no more than are left")
        if requested is not None and weeks > requested + 1e-9:
            raise DecisionRefused("approved weeks can't be more than were asked for")
        if qualifying is not None and weeks > qualifying + 1e-9:
            raise DecisionRefused("approved weeks must be no more than qualify")
        if requested is not None and weeks < requested - 1e-9:
            # Approving less than was asked refuses the rest: a partial denial.
            needs_manager("approving fewer weeks than asked is a partial denial: it")
            needs_reason("a partial denial")
        if route["route"] != "specialist_sign_off":
            needs_reason("approving a request sent to HR review")
    elif outcome is Outcome.DENY:
        needs_manager("denying leave")
        needs_reason("denying leave")
        if weeks:
            raise DecisionRefused("a denial can't carry weeks")
    elif outcome is Outcome.EXTEND:
        if member.rank < Role.HR_DIRECTOR:
            raise DecisionRefused("extending leave past the entitlement needs an HR director")
        needs_reason("extending leave")
        if not eligible:
            raise DecisionRefused(
                "an extension is for an eligible employee with a qualifying reason; a person "
                "decides anything else outside this step"
            )
        past = remaining is not None and (
            remaining <= 0 or (requested is not None and requested > remaining + 1e-9)
        )
        if not past:
            raise DecisionRefused("an extension is only for leave past the entitlement")
        if weeks <= 0:
            raise DecisionRefused("an extension needs the weeks it grants")
        if requested is not None and weeks > requested + 1e-9:
            raise DecisionRefused("extended weeks can't be more than were asked for")
    return member


def _refuse(audit: AuditLog, request_id: str, decision: LeaveDecision, known: bool, why: str):
    audit.append(
        request_id,
        "leave_decision_refused",
        f"hr:{decision.staff_id}" if known else "hr:?",
        {
            "refused_because": why,
            "submitted_hmac": audit.keyed_hash(canonical(decision.model_dump(mode="json"))),
        },
    )
    raise DecisionRefused(why)


def decide(
    audit: AuditLog,
    request_id: str,
    decision: LeaveDecision,
    staff: dict[str, StaffMember] | None = None,
) -> dict:
    """Check the decision and record it: the request's one final decision, or, for a request
    for more information, a step that leaves it waiting. Every refusal of a prepared request is
    logged with why, and raises DecisionRefused."""
    staff = load_staff() if staff is None else staff
    known = decision.staff_id in staff
    if not audit.entries(request_id):
        raise DecisionRefused(f"{request_id} isn't in the audit log: it wasn't prepared")
    if audit.has(request_id, "leave_decision"):
        _refuse(
            audit,
            request_id,
            decision,
            known,
            f"the audit log shows {request_id} was already decided",
        )
    try:
        found = evidence(audit, request_id)
    except EvidenceChanged:
        _refuse(
            audit, request_id, decision, known, "the evidence pack was changed after it was stored"
        )
    except DecisionRefused as exc:
        _refuse(audit, request_id, decision, known, str(exc))
    if found is None:
        _refuse(audit, request_id, decision, known, f"{request_id} has no evidence pack yet")
    digest, pack = found
    try:
        member = validate(decision, request_id, digest, pack, staff)
    except DecisionRefused as exc:
        _refuse(audit, request_id, decision, known, str(exc))
    base = {
        "decided_by": f"hr:{member.id}",
        "role": member.role,
        "outcome": decision.outcome.value,
        "evidence_sha256": digest,
        "reason_hmac": audit.keyed_hash(decision.reason),
        "signed_with_demo_key": member.demo_key,
        "decided_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if decision.outcome is Outcome.REQUEST_INFORMATION:
        audit.append(request_id, "leave_information_requested", f"hr:{member.id}", base)
        return base
    signed = decision.model_dump(mode="json") | {"request_id": request_id}
    stored = sha256(evidence_body(signed))  # its store key, known before it is stored
    route = pack["route"]
    payload = base | {
        "weeks": decision.weeks,
        "code_route": route["route"],
        "code_decided_by": route["decided_by"],
        "code_reasons": route["reasons"],
        "decision_hmac": audit.keyed_hash(stored),
    }
    try:
        audit.append(request_id, "leave_decision", f"hr:{member.id}", payload, once=True)
    except AlreadyRecorded:
        _refuse(
            audit,
            request_id,
            decision,
            known,
            f"the audit log shows {request_id} was already decided",
        )
    # Only the decision that won is stored: reason and signature, erasable, not in the chain.
    audit.put_evidence(signed)
    return payload


def status(audit: AuditLog, request_id: str) -> dict:
    """Where a request stands, from the log, with the route the evidence pack recorded (a run
    that lost the race to prepare it may have logged a different one after it)."""
    entries = audit.entries(request_id)
    if not entries:
        return {"request_id": request_id, "state": "unknown"}
    events = [e["event"] for e in entries]
    decided = next((e for e in entries if e["event"] == "leave_decision"), None)
    if decided is not None:
        return {"request_id": request_id, "state": "decided", "decision": decided["payload"]}
    if "leave_evidence" not in events:
        return {"request_id": request_id, "state": "being prepared, or stopped before it finished"}
    waiting = {
        "request_id": request_id,
        "state": "waiting for HR",
        "refused_decisions": events.count("leave_decision_refused"),
        "information_requested": events.count("leave_information_requested"),
    }
    try:
        found = evidence(audit, request_id)
    except (EvidenceChanged, DecisionRefused) as exc:
        return waiting | {"route": None, "evidence_problem": str(exc)}
    route = found[1]["route"]
    return waiting | {
        "route": route["route"],
        "decided_by": route["decided_by"],
        "reasons": route["reasons"],
    }
