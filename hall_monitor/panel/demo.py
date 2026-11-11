"""Demo data for the panel: the 36 synthetic claims through the real workflow, offline.

    uv run python -m hall_monitor.panel demo           # writes var/demo/

Every control runs as it does for real: the registry, the tool gateway, the routing rule, the
audit log, the approval gate with signed decisions, and the kill switch. Only the model is
scripted: each agent answers with the claim's answer key, so nothing calls the API and the
numbers say nothing about how well the agents work. The panel labels these pages as demo data.

The story the data tells: the first 30 claims go through; the fraud analytics lead then stops
the risk agent, so the last 6 stop at that step and go to a person; adjusters decide most of
what was routed to them, one overrides the AI, one decision is refused by the rules, and the
rest wait in the review queue.

Then the 28 synthetic leave requests go through the leave agents and the rules (scripted the
same way) into the same log, and HR signs some: a leave specialist signs off six clean requests
and leaves two waiting, an HR manager denies two, an HR director approves two of the three weeks
one employee asked for, a specialist asks for more information on the unclear one, and a
specialist's attempt to deny is refused by the rules. The rest wait for HR.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import yaml

from hall_monitor.agents.base import escape
from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.governance.approval import AdjusterDecision
from hall_monitor.governance.signing import demo_private_key
from hall_monitor.hr import signoff
from hall_monitor.hr.data import load_company, load_employees, load_messages, load_requests
from hall_monitor.hr.models import LeaveExplanation, LeaveIntake
from hall_monitor.hr.pipeline import prepare
from hall_monitor.kill_switch import STOPS_FILENAME, add_stop
from hall_monitor.models import (
    CoverageFinding,
    CriticReview,
    NextStep,
    Recommendation,
    RiskAssessment,
)
from hall_monitor.registry import APPROVALS_FILENAME, DEFAULT_REGISTRY_DIR, Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import open_workflow

DEFAULT_OUT = DEFAULT_REGISTRY_DIR.parent / "var" / "demo"
STOP_AFTER = 30  # claims triaged before the risk agent is stopped
STOPPED_AGENT, STOPPED_BY = "risk_fraud", "own-302"


class _ScriptedMessages:
    """Answers each agent with the claim's answer key, by output type, with fixed token counts."""

    def __init__(self, claim_id: str):
        truth = load_ground_truth(claim_id)
        self.answers = {
            type(truth.extraction): truth.extraction,
            CoverageFinding: CoverageFinding(**truth.coverage.model_dump(exclude={"decision"})),
            RiskAssessment: RiskAssessment(**truth.risk.model_dump(), reasoning="answer key"),
            CriticReview: CriticReview(issues=[]),
            Recommendation: Recommendation(next_step=truth.next_step, reasons=["answer key"]),
        }

    def parse(self, **kwargs):
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=self.answers[kwargs["output_format"]],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


class _ScriptedLeaveMessages:
    """Answers the leave intake agent with the request each message describes (the answer key),
    and the explanation agent with a plain note per check."""

    def __init__(self):
        self.requests = {r.id: r for r in load_requests()}
        self.messages = load_messages()

    def parse(self, **kwargs):
        content = kwargs["messages"][0]["content"]
        if kwargs["output_format"] is LeaveIntake:
            found = next(m for m in self.messages if escape(m.text[:60]) in content)
            fields = set(LeaveIntake.model_fields)
            answer = LeaveIntake(**self.requests[found.id].model_dump(include=fields))
        else:
            ids = ("employer", "service", "hours", "worksite", "reason", "entitlement")
            answer = LeaveExplanation(
                checks=[{"id": c, "text": f"The {c} check is as the code found."} for c in ids],
                summary="Start with the reasons the rules gave.",
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=answer,
            usage=SimpleNamespace(input_tokens=600, output_tokens=120),
        )


# Who decides what in the demo: (request, staff, outcome, weeks, reason).
LEAVE_DECISIONS = (
    *((r, "hrs-101", "approve", 0, "") for r in ("lr_001", "lr_003", "lr_005", "lr_007")),
    ("lr_010", "hrs-101", "approve", 0, ""),
    ("lr_020", "hrs-101", "approve", 0, ""),
    ("lr_002", "hrs-201", "deny", 0, "Under 12 months of service when the leave starts."),
    ("lr_018", "hrs-201", "deny", 0, "A parent-in-law isn't a family member FMLA covers."),
    ("lr_009", "hrs-301", "approve", 2, "Two FMLA weeks left; the third goes to company policy."),
    ("lr_019", "hrs-101", "request_information", 0, "Please tell us which kind of leave this is."),
    ("lr_006", "hrs-101", "deny", 0, "Fewer than 50 employees within 75 miles of the site."),
)


def _leave_demo(registry_dir: Path, audit_db: Path) -> dict:
    """Prepare every synthetic leave request into the log and sign the demo decisions."""
    from hall_monitor.governance.audit import AuditLog

    audit = AuditLog(audit_db)
    registry = Registry.from_dir(registry_dir)
    client = SimpleNamespace(messages=_ScriptedLeaveMessages())
    company, employees = load_company(), load_employees()
    try:
        prepared = {
            m.id: prepare(
                m,
                employees=employees,
                company=company,
                registry=registry,
                client=client,
                audit=audit,
            )
            for m in load_messages()
        }
        staff = signoff.load_staff()
        outcomes = {}
        for request_id, staff_id, outcome, weeks, reason in LEAVE_DECISIONS:
            asked = prepared[request_id].assessment.requested_weeks
            digest = signoff.evidence(audit, request_id)[0]
            decision = signoff.LeaveDecision(
                staff_id=staff_id,
                outcome=outcome,
                weeks=weeks or (asked if outcome == "approve" else 0),
                reason=reason,
                evidence_sha256=digest,
            ).signed(request_id, demo_private_key(staff_id))
            try:
                outcomes[request_id] = signoff.decide(audit, request_id, decision, staff)["outcome"]
            except signoff.DecisionRefused:
                outcomes[request_id] = "refused"
        waiting = [r for r in prepared if signoff.status(audit, r)["state"] == "waiting for HR"]
    finally:
        audit.close()
    return {"leave_requests": len(prepared), "leave_outcomes": outcomes, "leave_waiting": waiting}


def _complaint_demo(registry_dir: Path, audit_db: Path, limit: int = 60) -> dict:
    """The first complaints in the pinned CFPB snapshot, triaged with the scripted stand-in
    (complaints/scripted.py), which labels every answer as scripted. No pull: the demo has no
    network, so the panel's pull table is empty."""
    from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
    from hall_monitor.complaints.run import run as triage_complaints
    from hall_monitor.complaints.scripted import scripted_client
    from hall_monitor.governance.audit import AuditLog

    audit = AuditLog(audit_db)
    try:
        summary = triage_complaints(
            load_pinned(PINNED_NAME)[:limit],
            audit,
            scripted_client(),
            Registry.from_dir(registry_dir),
        )
    finally:
        audit.close()
    return {"complaints": summary["triaged"]}


def _registry_copy(out: Path) -> Path:
    registry = out / "registry"
    shutil.copytree(
        DEFAULT_REGISTRY_DIR, registry, ignore=shutil.ignore_patterns(".*", "*.schema.json")
    )
    # A fresh stop list, so no stop the approvals clear: drop those clearances too, or the
    # registry would refuse every agent (approvals may only clear stops the list holds).
    (registry / STOPS_FILENAME).write_text("stops: []\n")
    approvals = yaml.safe_load((registry / APPROVALS_FILENAME).read_text())
    for entry in approvals["agents"].values():
        entry.pop("cleared_stops", None)
    (registry / APPROVALS_FILENAME).write_text(yaml.safe_dump(approvals, sort_keys=True))
    return registry


def _decide(workflow, claim_id: str, adjuster: str, *, payout: float | None = None) -> str:
    """Accept the AI's recommendation, or pay `payout` instead, signed with the demo key."""
    status = workflow.status(claim_id)
    evidence = status["evidence"]
    next_step = NextStep(evidence["recommendation"]["next_step"])
    if payout is None:
        payout = evidence["coverage"]["covered_amount"] if next_step is NextStep.PAY else 0.0
        reason = "Checked the documents against the policy; the recommendation stands."
    else:
        next_step = NextStep.PAY
        reason = "Receipt for one item is a duplicate of another; paying the rest only."
    decision = AdjusterDecision(
        adjuster_id=adjuster,
        next_step=next_step,
        payout_usd=payout,
        reason=reason,
        evidence_sha256=status["evidence_sha256"],
    ).signed(claim_id, demo_private_key(adjuster))
    return workflow.decide(claim_id, decision)["state"]


def build(
    out: Path = DEFAULT_OUT, *, limit: int | None = None, stop_after: int = STOP_AFTER
) -> dict:
    """Write a demo registry, audit log and checkpoints under `out`, replacing any there.
    limit and stop_after make a smaller demo (the tests use one)."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    registry_dir = _registry_copy(out)
    audit_db, checkpoints = out / "audit.sqlite", out / "checkpoints.sqlite"
    wording = load_policy_wording()
    claims = list(iter_claims())[:limit]

    for n, claim in enumerate(claims):
        if n == stop_after:
            add_stop(
                registry_dir / STOPS_FILENAME,
                agent=STOPPED_AGENT,
                by=STOPPED_BY,
                reason="Risk scores drifted after a vendor data change; holding until reviewed.",
                now=datetime.now(UTC).replace(microsecond=0),
            )
        registry = Registry.from_dir(registry_dir)
        client = SimpleNamespace(messages=_ScriptedMessages(claim.claim_id))
        with open_workflow(
            checkpoints,
            audit_db,
            registry=registry,
            gateway=ToolGateway(registry),
            wording=wording,
            client=client,
        ) as workflow:
            workflow.submit(claim)

    registry = Registry.from_dir(registry_dir)
    with open_workflow(
        checkpoints, audit_db, registry=registry, gateway=ToolGateway(registry), wording=wording
    ) as workflow:
        triaged_before_stop = {c.claim_id for c in claims[:stop_after]}
        to_decide = [c for c in workflow.waiting() if c in triaged_before_stop]

        def ai_pays(claim_id: str) -> float:
            evidence = workflow.status(claim_id)["evidence"]
            if evidence["recommendation"]["next_step"] != NextStep.PAY.value:
                return 0.0
            return evidence["coverage"]["covered_amount"] or 0.0

        payouts = {c: ai_pays(c) for c in to_decide}
        # The largest payout goes to the junior adjuster first, beyond their authority, so the
        # rules refuse it and a senior adjuster decides; the next largest is overridden.
        by_size = sorted(to_decide, key=lambda c: payouts[c], reverse=True)
        refused, overridden = by_size[0], by_size[1]
        left_waiting = set(to_decide[-3:]) - {refused, overridden}
        outcomes = {}
        for claim_id in to_decide:
            if claim_id in left_waiting:
                continue
            if claim_id == refused and _decide(workflow, claim_id, "adj-101") == "decided":
                outcomes[claim_id] = "decided"  # it was within their authority after all
                continue
            if claim_id == overridden:
                payout = round(payouts[claim_id] * 0.6, 2)
                outcomes[claim_id] = _decide(workflow, claim_id, "adj-201", payout=payout)
            else:
                outcomes[claim_id] = _decide(workflow, claim_id, "adj-201")
        still_waiting = workflow.waiting()
    return (
        {
            "out": str(out),
            "claims": len(claims),
            "decided_by_adjusters": sum(s == "decided" for s in outcomes.values()),
            "waiting": still_waiting,
        }
        | _leave_demo(registry_dir, audit_db)
        | _complaint_demo(registry_dir, audit_db)
    )
