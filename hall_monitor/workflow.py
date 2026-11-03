"""Claim workflow: triage, then either the automatic route or the adjuster approval gate.

A LangGraph graph with a SQLite checkpointer. Every step is also written to the
hash-chained audit log (governance/audit.py). When the routing rule sends a claim to a person,
the graph stops at the review node (an interrupt) with the evidence pack, and its state is
saved, so the claim can wait for an adjuster across process restarts. The adjuster's decision
resumes it; a decision the rules reject stops it at review again with the reason. One thread
per claim, keyed by claim ID.
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

from hall_monitor.governance.approval import (
    AdjusterDecision,
    DecisionRejected,
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
    canonical,
    record_triage,
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
) -> StateGraph:
    adjusters = load_adjusters()

    def run_triage(state: ClaimState) -> ClaimState:
        claim = ClaimSubmission.model_validate(state["claim"])
        result = triage(
            claim, registry=registry, gateway=gateway, wording=wording, client=client, critic=critic
        )
        pack = build_evidence_pack(result, claim)
        record_triage(audit, claim, result)
        audit.append(
            claim.claim_id, "evidence_pack", "pipeline", {"evidence_sha256": evidence_sha256(pack)}
        )
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
        final = final_auto(result, state["evidence_sha256"]).model_dump(mode="json")
        audit.append(result.claim_id, "final_decision", "auto", final)
        return {"final": final}

    def review(state: ClaimState) -> ClaimState:
        submitted = interrupt(
            {
                "claim_id": state["result"]["claim_id"],
                "evidence": state["evidence"],
                "evidence_sha256": state["evidence_sha256"],
                "previous_decision_rejected": state.get("rejected"),
            }
        )
        result = TriageResult.model_validate(state["result"])
        try:
            decision = AdjusterDecision.model_validate(submitted)
            adjuster = validate_decision(decision, result, state["evidence_sha256"], adjusters)
        except (DecisionRejected, ValueError) as exc:
            audit.append(
                result.claim_id,
                "decision_rejected",
                f"adjuster:{submitted.get('adjuster_id') if isinstance(submitted, dict) else '?'}",
                {"rejected_because": str(exc), "submitted_sha256": sha256(canonical(submitted))},
            )
            return {"rejected": str(exc)}
        final = final_from_adjuster(decision, adjuster, result, state["evidence_sha256"])
        audit.append(
            result.claim_id, "final_decision", final.decided_by, final.model_dump(mode="json")
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

    def submit(self, claim: ClaimSubmission) -> dict:
        """Triage a new claim. Returns its status (see status())."""
        if self.app.get_state(self._config(claim.claim_id)).values:
            raise ValueError(f"{claim.claim_id} is already in the workflow")
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
            return {
                "claim_id": claim_id,
                "state": "waiting_for_adjuster",
                "evidence": values["evidence"],
                "evidence_sha256": values["evidence_sha256"],
                "routing_reasons": values["result"]["routing"]["reasons"],
                "previous_decision_rejected": values.get("rejected"),
            }
        return {"claim_id": claim_id, "state": "in_progress"}

    def waiting(self) -> list[str]:
        """Claims waiting for an adjuster."""
        threads = {c.config["configurable"]["thread_id"] for c in self.saver.list(None)}
        return sorted(t for t in threads if self.status(t)["state"] == "waiting_for_adjuster")
