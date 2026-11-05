"""Claim workflow: triage, then either the automatic route or the adjuster approval gate.

A LangGraph graph with a SQLite checkpointer. Every step is also written to the
hash-chained audit log (governance/audit.py) as it happens, and the evidence pack is stored
there under its hash. When the routing rule sends a claim to a person, the graph stops at the
review node (an interrupt) with the evidence pack, and its state is saved, so the claim can
wait for an adjuster across process restarts. Checkpoints aren't tamper-evident, so before any
decision the review node checks the saved pack against the hash in the audit log. The
adjuster's signed decision resumes the claim; a decision the rules reject stops it at review
again with the reason. One thread per claim, keyed by claim ID.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict

import anthropic
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from hall_monitor.governance.approval import (
    AdjusterDecision,
    DecisionRejected,
    audit_payload,
    build_evidence_pack,
    evidence_sha256,
    final_auto,
    final_from_adjuster,
    load_adjusters,
    validate_decision,
)
from hall_monitor.governance.audit import (
    DEFAULT_AUDIT_DB,
    AuditLog,
    EvidenceChanged,
    TriageRecorder,
    canonical,
    sha256,
)
from hall_monitor.models import ClaimSubmission, Route, TriageResult
from hall_monitor.pipeline import triage
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway

DEFAULT_CHECKPOINTS = Path(__file__).resolve().parents[1] / "var" / "checkpoints.sqlite"


class ClaimState(TypedDict, total=False):
    # Plain JSON, so the checkpoint is readable without this code's classes.
    claim: dict
    result: dict
    evidence: dict
    evidence_sha256: str
    rejected: str | None
    final: dict


def build_graph(
    *,
    registry: Registry,
    gateway: ToolGateway,
    wording: str,
    audit: AuditLog,
    client: anthropic.Anthropic | None = None,
    critic: bool = True,
    model_routing: bool = False,
) -> StateGraph:
    adjusters = load_adjusters()

    def run_triage(state: ClaimState) -> ClaimState:
        claim = ClaimSubmission.model_validate(state["claim"])
        result = triage(
            claim,
            registry=registry,
            gateway=gateway,
            wording=wording,
            client=client,
            critic=critic,
            model_routing=model_routing,
            on_event=TriageRecorder(audit, claim.claim_id),
        )
        pack = build_evidence_pack(result, claim)
        digest = audit.put_evidence(pack)
        audit.append(claim.claim_id, "evidence_pack", "pipeline", {"evidence_sha256": digest})
        return {
            "result": result.model_dump(mode="json"),
            "evidence": pack,
            "evidence_sha256": evidence_sha256(pack),
            "rejected": None,
        }

    def route(state: ClaimState) -> str:
        return "auto" if state["result"]["routing"]["route"] == Route.AUTO.value else "review"

    def auto(state: ClaimState) -> ClaimState:
        result = TriageResult.model_validate(state["result"])
        final = final_auto(result, state["evidence_sha256"])
        audit.append(
            result.claim_id,
            "final_decision",
            "auto",
            audit_payload(final, audit.keyed_hash(final.reason)),
        )
        return {"final": final.model_dump(mode="json")}

    def review(state: ClaimState) -> ClaimState:
        submitted = interrupt(
            {
                "claim_id": state["result"]["claim_id"],
                "evidence": state["evidence"],
                "evidence_sha256": state["evidence_sha256"],
                "previous_decision_rejected": state.get("rejected"),
            }
        )
        claim_id = state["claim"]["claim_id"]
        try:
            try:
                result = TriageResult.model_validate(state["result"])
            except ValidationError:  # edited until it no longer parses
                raise DecisionRejected(
                    "the saved triage result isn't the one recorded at triage"
                ) from None
            _check_evidence(audit, claim_id, state, result)
            decision = AdjusterDecision.model_validate(submitted)
            adjuster = validate_decision(decision, result, state["evidence_sha256"], adjusters)
        except (DecisionRejected, EvidenceChanged, ValueError) as exc:
            reason = _rejection(exc)
            audit.append(
                claim_id,
                "decision_rejected",
                f"adjuster:{submitted['adjuster_id']}"
                if _known(submitted, adjusters)
                else "adjuster:?",
                {"rejected_because": reason, "submitted_sha256": sha256(canonical(submitted))},
            )
            return {"rejected": reason}
        final = final_from_adjuster(decision, adjuster, result, state["evidence_sha256"])
        audit.append(
            result.claim_id,
            "final_decision",
            final.decided_by,
            audit_payload(final, audit.keyed_hash(final.reason), decision.signature),
        )
        return {"final": final.model_dump(mode="json"), "rejected": None}

    def after_review(state: ClaimState) -> str:
        return "review" if state.get("rejected") else END

    graph = StateGraph(ClaimState)
    graph.add_node("triage", run_triage)
    graph.add_node("auto", auto)
    graph.add_node("review", review)
    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route, {"auto": "auto", "review": "review"})
    graph.add_edge("auto", END)
    graph.add_conditional_edges("review", after_review, {"review": "review", END: END})
    return graph


def _known(submitted, adjusters) -> bool:
    """Whether the submission names a registered adjuster, so the log may name them by ID."""
    return isinstance(submitted, dict) and submitted.get("adjuster_id") in adjusters


def _rejection(exc: Exception) -> str:
    """Why a decision was rejected, safe for the log: our own messages carry IDs and amounts
    only, and a malformed submission is summarised by error type, because pydantic's message
    repeats the submitted values (and an unknown field's name is submitted text too)."""
    if isinstance(exc, ValidationError):
        kinds = ", ".join(sorted({e["type"] for e in exc.errors()}))
        return f"the decision isn't valid ({kinds})"
    return str(exc)


def _check_evidence(
    audit: AuditLog, claim_id: str, state: ClaimState, result: TriageResult
) -> None:
    """The saved pack must be the one the audit log recorded at triage, and still stored
    unchanged: a checkpoint edited after triage can't put different evidence in front of the
    adjuster. The saved result must be the one recorded too, because the approval rules
    (authority, senior adjuster, payout bounds) read it: an edit can't relax them."""
    if sha256(result.model_dump_json()) != audit.result_hash(claim_id):
        raise DecisionRejected("the saved triage result isn't the one recorded at triage")
    logged = audit.evidence_hash(claim_id)
    if logged is None or state["evidence_sha256"] != logged:
        raise DecisionRejected("the saved evidence pack isn't the one recorded at triage")
    if evidence_sha256(state["evidence"]) != logged:
        raise DecisionRejected("the saved evidence pack was changed after triage")
    if audit.get_evidence(logged) is None:
        raise DecisionRejected("the evidence pack recorded at triage is no longer stored")


@contextmanager
def open_workflow(
    checkpoints: Path = DEFAULT_CHECKPOINTS, audit_db: Path = DEFAULT_AUDIT_DB, **graph_kwargs
) -> Iterator[ClaimWorkflow]:
    checkpoints.parent.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(audit_db)
    try:
        with SqliteSaver.from_conn_string(str(checkpoints)) as saver:
            app = build_graph(audit=audit, **graph_kwargs).compile(checkpointer=saver)
            yield ClaimWorkflow(app, saver, audit)
    finally:
        audit.close()


class ClaimWorkflow:
    def __init__(self, app, saver: SqliteSaver, audit: AuditLog):
        self.app = app
        self.saver = saver
        self.audit = audit

    @staticmethod
    def _config(claim_id: str) -> dict:
        return {"configurable": {"thread_id": claim_id}}

    def submit(self, claim: ClaimSubmission, *, restart: bool = False) -> dict:
        """Triage a new claim. Returns its status (see status()).

        A claim whose triage started but has no result is either still running somewhere or
        crashed, and the two look the same from here, so it is only started again with
        restart=True (`run --fresh`). Its earlier events stay in the audit log.
        """
        existing = self.app.get_state(self._config(claim.claim_id)).values
        if existing and "result" in existing:
            raise ValueError(f"{claim.claim_id} is already in the workflow")
        if self.audit.evidence_hash(claim.claim_id) is not None:
            # The log, not the checkpoint file, says whether triage finished: new or deleted
            # checkpoints must not let a decided claim be triaged, and paid, again.
            raise ValueError(
                f"the audit log shows {claim.claim_id} already finished triage; resume it from "
                "its checkpoints, or have a person handle it"
            )
        if existing and not restart:
            raise ValueError(
                f"{claim.claim_id}'s triage started but didn't finish; if it isn't still "
                "running, restart it (restart=True, or run --fresh)"
            )
        if existing:
            self.saver.delete_thread(claim.claim_id)
        self.app.invoke({"claim": claim.model_dump(mode="json")}, self._config(claim.claim_id))
        return self.status(claim.claim_id)

    def decide(self, claim_id: str, decision: AdjusterDecision) -> dict:
        """Resume a claim waiting for an adjuster. Returns its status."""
        if self.status(claim_id)["state"] != "waiting_for_adjuster":
            raise ValueError(f"{claim_id} is not waiting for an adjuster")
        self.app.invoke(Command(resume=decision.model_dump(mode="json")), self._config(claim_id))
        return self.status(claim_id)

    def status(self, claim_id: str) -> dict:
        snapshot = self.app.get_state(self._config(claim_id))
        values = snapshot.values
        if not values:
            return {"claim_id": claim_id, "state": "unknown"}
        if "final" in values:
            return {"claim_id": claim_id, "state": "decided", "final": values["final"]}
        if "review" in snapshot.next:
            # Show the pack stored under the hash the audit log recorded at triage, not the
            # checkpoint's copy, and say if the checkpoint no longer matches it.
            logged = self.audit.evidence_hash(claim_id)
            try:
                result = TriageResult.model_validate(values["result"])
                _check_evidence(self.audit, claim_id, values, result)
                problem = None
            except (DecisionRejected, EvidenceChanged) as exc:
                problem = str(exc)
            except ValueError:  # the saved result no longer parses: edited, so not trusted
                problem = "the saved triage result isn't the one recorded at triage"
            try:
                stored = self.audit.get_evidence(logged) if logged else None
            except EvidenceChanged:
                stored = None
            return {
                "claim_id": claim_id,
                "state": "waiting_for_adjuster",
                "evidence": stored if stored is not None else values["evidence"],
                "evidence_sha256": logged or values["evidence_sha256"],
                "evidence_problem": problem,
                "routing_reasons": values["result"]["routing"]["reasons"],
                "previous_decision_rejected": values.get("rejected"),
            }
        return {"claim_id": claim_id, "state": "in_progress"}

    def waiting(self) -> list[str]:
        """Claims waiting for an adjuster."""
        # One row per claim, not one per saved checkpoint version.
        rows = self.saver.conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
        return sorted(t for (t,) in rows if self.status(t)["state"] == "waiting_for_adjuster")
