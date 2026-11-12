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
  keeps waiting; each request for information is stored, signed, and recorded once.

The audit log, not any other file, says whether a request was decided: one final decision per
request, refused if there is one already. Every refusal that reaches these rules, for a
request in the log, is logged with why (under the staff ID only if they signed it). What the
log keeps of a decision is impersonal: the staff ID and role, the outcome, the weeks, and keyed
hashes of the reason and of the signed decision. The signed decision itself (with its reason
and signature) is stored, before the log entry is written, beside the
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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hall_monitor.governance import signing
from hall_monitor.governance.audit import (
    AlreadyRecorded,
    AuditLog,
    EvidenceChanged,
    canonical,
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
    """The staff file, refused whole (ValueError, without the file's values) if it doesn't
    parse, an entry has a field it shouldn't, names a role the rules don't know, or an ID is
    listed twice (the later entry could otherwise change someone's role)."""
    try:
        entries = yaml.safe_load(path.read_text())["staff"]
        members = [StaffMember.model_validate(m) for m in entries]
    except yaml.YAMLError:
        raise ValueError(f"{path.name} isn't valid YAML") from None
    except (KeyError, TypeError):
        raise ValueError(f"{path.name} has no list of staff") from None
    except ValidationError as exc:
        fields = ", ".join(sorted({".".join(map(str, e["loc"])) for e in exc.errors()}))
        raise ValueError(f"{path.name} has an entry that isn't valid ({fields})") from None
    seen: dict[str, StaffMember] = {}
    for member in members:
        if member.role not in {r.label for r in Role}:
            raise ValueError(f"{member.id}: its role isn't one the rules know")
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
        # How it was prepared (ADR 0028): the supervisor's path, and the re-check's intake to
        # compare with the request when two intakes disagreed.
        "mode": getattr(prepared, "mode", "fixed"),
        "supervisor_path": [s.action.value for s in getattr(prepared, "supervisor_steps", [])],
        "recheck_intake": None
        if getattr(prepared, "recheck_intake", None) is None
        else prepared.recheck_intake.model_dump(mode="json"),
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
        if requested is None and "bonding_window" in route["reasons"]:
            raise DecisionRefused(
                "intermittent bonding leave running past its 12 months can't be measured "
                "against them here; a person decides it outside this step"
            )
        # Approving less than was asked (or, for intermittent leave, than is left) refuses the
        # rest: a partial denial.
        asked = requested if requested is not None else remaining
        if weeks < asked - 1e-9:
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
        # weeks is the total granted for this request, entitlement included.
        if weeks <= max(remaining, 0) + 1e-9:
            raise DecisionRefused(
                "an extension needs the weeks it grants, more weeks than are left; approving "
                "within what is left is an approval"
            )
        if requested is not None and weeks > requested + 1e-9:
            raise DecisionRefused("extended weeks can't be more than were asked for")
    return member


def _signed_by(decision: LeaveDecision, request_id: str, staff: dict[str, StaffMember]) -> bool:
    """The decision names registered staff and carries their valid signature."""
    member = staff.get(decision.staff_id)
    message = signing.decision_message(request_id, decision.model_dump(mode="json"))
    return member is not None and signing.verify(message, decision.signature, member.public_key)


def _refuse(audit: AuditLog, request_id: str, decision: LeaveDecision, signed: bool, why: str):
    """Log the refusal (under the staff ID only if they signed it) and raise."""
    audit.append(
        request_id,
        "leave_decision_refused",
        f"hr:{decision.staff_id}" if signed else "hr:?",
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
    for more information, a step that leaves it waiting. Every refusal that reaches these rules,
    for a request in the log, is logged with why and raises DecisionRefused."""
    staff = load_staff() if staff is None else staff
    signed = _signed_by(decision, request_id, staff)
    entries = audit.entries(request_id)
    if not any(e["event"] == "leave_request_submitted" for e in entries):
        # Not logged: a mistyped ID (a claim's, say) mustn't gain leave entries.
        raise DecisionRefused(f"{request_id} isn't a leave request in the audit log")
    already = f"the audit log shows {request_id} was already decided"
    if audit.has(request_id, "leave_decision"):
        _refuse(audit, request_id, decision, signed, already)
    try:
        found = evidence(audit, request_id)
    except EvidenceChanged:
        _refuse(
            audit, request_id, decision, signed, "the evidence pack was changed after it was stored"
        )
    except DecisionRefused as exc:
        _refuse(audit, request_id, decision, signed, str(exc))
    if found is None:
        _refuse(audit, request_id, decision, signed, f"{request_id} has no evidence pack yet")
    digest, pack = found
    try:
        member = validate(decision, request_id, digest, pack, staff)
    except DecisionRefused as exc:
        _refuse(audit, request_id, decision, signed, str(exc))
    # The signed record, with its reason and signature, goes to the erasable store first, so a
    # logged decision always has it; the log keeps only its keyed hash.
    record = decision.model_dump(mode="json") | {"request_id": request_id}
    stored = audit.put_evidence(record)
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
        request_hmac = audit.keyed_hash(stored)
        asked = [
            e for e in audit.entries(request_id) if e["event"] == "leave_information_requested"
        ]
        if any(e["payload"].get("request_hmac") == request_hmac for e in asked):
            _refuse(
                audit,
                request_id,
                decision,
                signed,
                "this request for information is already recorded",
            )
        payload = base | {"request_hmac": request_hmac}
        audit.append(request_id, "leave_information_requested", f"hr:{member.id}", payload)
        return payload
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
        winner = next(e for e in audit.entries(request_id) if e["event"] == "leave_decision")
        if winner["payload"].get("decision_hmac") != payload["decision_hmac"]:
            audit.erase_evidence(stored)  # the loser's record: nothing refers to it
        _refuse(audit, request_id, decision, signed, already)
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
        if "leave_prepared" in events:
            return {
                "request_id": request_id,
                "state": "stopped before the rules ran; can be prepared again",
            }
        return {"request_id": request_id, "state": "being prepared"}
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
