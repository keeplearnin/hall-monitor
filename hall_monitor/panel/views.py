"""What the control panel shows, read from the governance core and from nothing else.

Every view asks the code that enforces the control: an agent's state comes from
Registry.require, the same check that refuses it at run time; the audit view is AuditLog.verify;
the eval view is the CI gate. Nothing here writes. The audit log is opened read-only, and the
checkpoints, which hold claim text, are never opened.

What the views return is as impersonal as the audit log itself: IDs, hashes, decision fields,
routing reasons and costs. Stop reasons, evidence packs, adjusters' reasons and the signatures
over them stay where they are (the stop list, the evidence store, the review CLI), because the
panel is shown to more people than need them.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from hall_monitor.approvals import AGENT_MODULES, prompt_sha256
from hall_monitor.evals import gate
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog, ChainHead
from hall_monitor.governance.controls import REGISTER, load_register
from hall_monitor.kill_switch import STOPS_FILENAME, load_stops
from hall_monitor.registry import (
    APPROVALS_FILENAME,
    DEFAULT_REGISTRY_DIR,
    AgentStoppedError,
    Registry,
    RegistryError,
    StopListError,
    UnapprovedManifestError,
    load_approvals,
    load_owners,
)

# The worked examples. Only travel claims write to the audit log today.
WORKFLOWS = (
    {"id": "travel_claims", "name": "Travel-insurance claims", "status": "built"},
    {"id": "hr_leave", "name": "Workday HR leave of absence", "status": "designed, no code yet"},
)


@dataclass(frozen=True)
class Sources:
    """Where the panel reads from. The defaults are the files the CLIs use."""

    registry_dir: Path = DEFAULT_REGISTRY_DIR
    audit_db: Path = DEFAULT_AUDIT_DB
    register: Path = REGISTER
    gate_config: Path = gate.CONFIG
    # Shown on every page when the audit log came from the demo builder's scripted model.
    demo: bool = False


@contextmanager
def _audit(src: Sources) -> Iterator[AuditLog | None]:
    """The audit log, read-only, or None if there isn't one yet."""
    if not src.audit_db.exists():
        yield None
        return
    log = AuditLog(src.audit_db, read_only=True)
    try:
        yield log
    finally:
        log.close()


def _entries(src: Sources) -> list[dict]:
    with _audit(src) as log:
        return log.entries() if log is not None else []


# --- Agents ----------------------------------------------------------------------------------


def _registry(src: Sources) -> tuple[Registry | None, str | None]:
    try:
        return Registry.from_dir(src.registry_dir), None
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        # The registry wouldn't load at run time either, so no agent can run.
        return None, f"the registry can't be loaded ({type(exc).__name__}); no agent can run"


def _stops(src: Sources) -> tuple[list, str | None]:
    try:
        return list(load_stops(src.registry_dir / STOPS_FILENAME).stops), None
    except (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError) as exc:
        return [], f"{STOPS_FILENAME} can't be read ({type(exc).__name__}); every agent is refused"


def agents(src: Sources) -> dict:
    """Every agent with its owner, risk tier, model, approval and whether it may run now.

    "May run" is Registry.require's answer, with the agent's current prompt template, so the
    panel can't disagree with the check that refuses it at run time.
    """
    registry, problem = _registry(src)
    stops, stop_problem = _stops(src)
    if registry is None:
        return {"agents": [], "problem": problem}
    owners = {o["id"]: o for o in load_owners(src.registry_dir / "owners.yaml")}
    approvals = load_approvals(src.registry_dir / APPROVALS_FILENAME)
    rows = []
    for manifest in registry:
        module = AGENT_MODULES.get(manifest.id)
        prompt = prompt_sha256(module) if module is not None else None
        approved = approvals.get(manifest.id, {})
        cleared = set(approved.get("cleared_stops", ()))
        try:
            registry.require(manifest.id, prompt_sha256=prompt)
            state, refused_because = "may run", None
        except AgentStoppedError:
            state, refused_because = "stopped", "stopped by its owner"
        except StopListError as exc:
            state, refused_because = "refused", str(exc)
        except UnapprovedManifestError as exc:
            state, refused_because = "unapproved", str(exc)
        except RegistryError as exc:
            state, refused_because = manifest.status.value, str(exc)
        owner = owners.get(manifest.owner, {})
        rows.append(
            {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "purpose": manifest.purpose,
                "owner": {
                    "id": manifest.owner,
                    "name": owner.get("name"),
                    "role": owner.get("role"),
                },
                "risk_tier": manifest.risk_tier.value,
                "status": manifest.status.value,
                "model": manifest.model,
                "economy_model": manifest.economy_model,
                "economy_max_claim_usd": manifest.economy_max_claim_usd,
                "allowed_tools": list(manifest.allowed_tools),
                "data_classes": [c.value for c in manifest.data_classes],
                "manifest_digest": manifest.digest(),
                "manifest_approved": approved.get("manifest_digest") == manifest.digest(),
                "prompt_version": approved.get("prompt_version"),
                "prompt_sha256": prompt,
                "prompt_approved": None
                if prompt is None
                else approved.get("prompt_sha256") == prompt,
                "state": state,
                "refused_because": refused_because,
                # The reason stays in the stop list: it may be personal.
                "stops": [
                    {
                        "id": s.id,
                        "stopped_by": s.stopped_by,
                        "stopped_at": s.stopped_at.isoformat(),
                        "cleared": s.id in cleared,
                    }
                    for s in stops
                    if s.agent == manifest.id
                ],
            }
        )
    return {"agents": rows, "problem": stop_problem}


def agent(src: Sources, agent_id: str) -> dict | None:
    """One agent, with the eval results that scored it and the controls that govern agents."""
    found = next((a for a in agents(src)["agents"] if a["id"] == agent_id), None)
    if found is None:
        return None
    scored = []
    for check in evals(src)["checks"]:
        for scored_agent in check["agents"]:
            if scored_agent["agent_id"] == agent_id:
                scored.append(
                    {
                        "check": check["name"],
                        "state": check["state"],
                        "version": scored_agent.get("agent_version"),
                        "current": scored_agent.get("manifest_digest") == found["manifest_digest"],
                    }
                )
    return found | {"evals": scored}


# --- Cases -----------------------------------------------------------------------------------


def _cases(entries: list[dict]) -> dict[str, dict]:
    """Each claim's state as the audit log records it. The log, not the checkpoints, decides."""
    cases: dict[str, dict] = {}
    for e in entries:
        case = cases.setdefault(
            e["claim_id"],
            {
                "claim_id": e["claim_id"],
                "submitted_at": None,
                "route": None,
                "routing_reasons": [],
                "recommended_next_step": None,
                "covered_amount": None,
                "stopped_reason": None,
                "cost_usd": 0.0,
                "evidence": False,
                "decided_by": None,
                "next_step": None,
                "payout_usd": None,
                "overrode_ai": False,
                "rejected_decisions": 0,
                "last_seq": e["seq"],
            },
        )
        case["last_seq"] = e["seq"]
        payload = e["payload"]
        match e["event"]:
            case "claim_submitted":
                case["submitted_at"] = case["submitted_at"] or e["recorded_at"]
            case "triage_result":
                case["route"] = payload.get("route")
                case["routing_reasons"] = payload.get("routing_reasons") or []
                case["recommended_next_step"] = payload.get("recommended_next_step")
                case["covered_amount"] = payload.get("covered_amount")
                case["stopped_reason"] = payload.get("stopped_reason")
                case["cost_usd"] += payload.get("cost_usd") or 0.0
            case "evidence_pack":
                case["evidence"] = True
            case "decision_rejected":
                case["rejected_decisions"] += 1
            case "final_decision" if case["decided_by"] is None:  # the first one stands
                case["decided_by"] = payload.get("decided_by")
                case["next_step"] = payload.get("next_step")
                case["payout_usd"] = payload.get("payout_usd")
                case["overrode_ai"] = bool(payload.get("overrode_ai"))
    for case in cases.values():
        case["state"] = (
            "decided"
            if case["decided_by"]
            else "waiting for a person"
            if case["evidence"]
            else "in triage, or stopped before it finished"
        )
    return cases


def cases(src: Sources) -> list[dict]:
    return sorted(_cases(_entries(src)).values(), key=lambda c: c["claim_id"])


def review_queue(src: Sources) -> list[dict]:
    """Claims the routing rule sent to a person that nobody has decided yet. Deciding stays in
    the review CLI, where the adjuster sees the evidence and signs."""
    return [c for c in cases(src) if c["state"] == "waiting for a person"]


def case_trace(src: Sources, claim_id: str) -> dict | None:
    """Every audit entry for one claim, in order. A signature is shown as present, not by value:
    with the public key it could confirm a guessed reason (HM-06 gap)."""
    with _audit(src) as log:
        entries = log.entries(claim_id) if log is not None else []
    if not entries:
        return None
    shown = []
    for e in entries:
        payload = dict(e["payload"])
        if "signature" in payload:
            payload["signature"] = "present (Ed25519)"
        shown.append({k: e[k] for k in ("seq", "recorded_at", "event", "actor", "entry_hash")})
        shown[-1]["payload"] = payload
    return {"claim_id": claim_id, "case": _cases(entries)[claim_id], "entries": shown}


def reason_kind(reason: str) -> str:
    """A routing reason without its case's figures, so reasons can be counted together."""
    if reason.startswith("fraud flags:"):
        return "fraud flags"
    return re.sub(r"^payout [\d,.]+ above", "payout above", reason)


# --- Overview --------------------------------------------------------------------------------


def overview(src: Sources) -> dict:
    """Per workflow: cases, the share sent to a person, overrides, stopped cases, cost; and any
    agent that can't run, and whether the audit chain verifies."""
    all_cases = cases(src)
    triaged = [c for c in all_cases if c["route"] is not None]
    decided = [c for c in all_cases if c["decided_by"]]
    by_person = [c for c in decided if c["decided_by"] != "auto"]
    cost = sum(c["cost_usd"] for c in all_cases)
    roster = agents(src)
    integrity = audit_integrity(src)
    travel = {
        "cases": len(all_cases),
        "triaged": len(triaged),
        "sent_to_a_person": sum(c["route"] == "adjuster" for c in triaged),
        "decided_automatically": len(decided) - len(by_person),
        "decided_by_a_person": len(by_person),
        "waiting_for_a_person": sum(c["state"] == "waiting for a person" for c in all_cases),
        "overrides": sum(c["overrode_ai"] for c in by_person),
        "stopped_cases": sum(c["stopped_reason"] is not None for c in triaged),
        "rejected_decisions": sum(c["rejected_decisions"] for c in all_cases),
        "cost_usd": round(cost, 4),
        "cost_per_case_usd": round(cost / len(triaged), 4) if triaged else None,
        "routing_reasons": Counter(
            reason_kind(r) for c in triaged for r in c["routing_reasons"]
        ).most_common(),
    }
    return {
        "workflows": [
            w | ({"metrics": travel} if w["id"] == "travel_claims" else {}) for w in WORKFLOWS
        ],
        "agents_not_running": [
            {"id": a["id"], "state": a["state"], "owner": a["owner"]["id"]}
            for a in roster["agents"]
            if a["state"] != "may run"
        ],
        "registry_problem": roster["problem"],
        "audit": {k: integrity[k] for k in ("ok", "entries", "head", "problems", "exists")},
        "demo": src.demo,
    }


# --- Audit integrity -------------------------------------------------------------------------


def audit_integrity(src: Sources, anchor: ChainHead | None = None) -> dict:
    """AuditLog.verify on a read-only open, against an anchored head when one is given: a head
    copied out earlier to somewhere the log's writers can't change."""
    with _audit(src) as log:
        if log is None:
            return {
                "exists": False,
                "ok": True,
                "entries": 0,
                "head": None,
                "anchor": None,
                "problems": [],
                "evidence_erased": [],
            }
        result = log.verify(expect=anchor)
    return {
        "exists": True,
        "ok": result.ok,
        "entries": result.entries,
        "head": None if result.head is None else vars(result.head),
        "anchor": None if anchor is None else vars(anchor),
        "problems": result.problems,
        "evidence_erased": result.evidence_erased,
    }


# --- Evals and controls ----------------------------------------------------------------------


def evals(src: Sources) -> dict:
    """The CI eval gate's own result, with each check's thresholds, values and the agent
    versions it scored. Published results only: no model calls."""
    approvals = load_approvals(src.registry_dir / APPROVALS_FILENAME)
    ok, results = gate.run_gate(src.gate_config, approvals)
    config = yaml.safe_load(src.gate_config.read_text())
    checks = []
    for spec, result in zip(config["checks"], results, strict=True):
        checks.append(
            {
                "name": result.name,
                "state": result.state,
                "required": bool(spec.get("required")),
                "results": spec["results"],
                "failures": result.failures,
                "notes": result.notes,
                "thresholds": [
                    {"metric": k, "rule": "min", "limit": v, "value": result.metrics.get(k)}
                    for k, v in spec.get("min", {}).items()
                ]
                + [
                    {"metric": k, "rule": "max", "limit": v, "value": result.metrics.get(k)}
                    for k, v in spec.get("max", {}).items()
                ],
                "run_at": result.meta.get("run_at"),
                "agents": result.meta.get("agents", []),
            }
        )
    return {"passed": ok, "stale_results": config["stale_results"], "checks": checks}


def controls(src: Sources) -> dict:
    """The controls register as published: code, tests, evidence and gaps, mapped to NIST AI RMF
    and FINOS AIGF, and what it says it doesn't cover."""
    register = load_register(src.register)
    return {
        "controls": [
            {
                "id": c["id"],
                "name": c["name"],
                "status": c["status"],
                "what": c["what"],
                "gaps": c.get("gaps"),
                "code": c["code"],
                "tests": c["tests"],
                "evidence": c["evidence"],
                "nist": c["nist"],
                "finos": c["finos"],
            }
            for c in register["controls"]
        ],
        "not_covered": register.get("not_covered", []),
    }


def file_sha256(path: Path) -> str | None:
    """For tests and the snapshot: proof the panel left a file as it was."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
