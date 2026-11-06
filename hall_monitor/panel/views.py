"""What the control panel shows, read from the governance core and from nothing else.

Every view asks the code that enforces the control: an agent's state comes from
Registry.require, the same check that refuses it at run time; the audit view is AuditLog.verify;
the eval view is the CI gate. Nothing here writes. The audit log is opened read-only, and the
checkpoints, which hold claim text, are never opened.

What the views return is what the audit log holds, less anything the model wrote as free text:
IDs, hashes, decision fields, reasons in the code's own words and costs. Kill-switch stop
reasons, evidence packs, adjusters' reasons and the signatures over them stay where they are
(the stop list, the evidence store, the review CLI), because the panel is shown to more people
than need them.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from hall_monitor.approvals import AGENT_MODULES, prompt_sha256
from hall_monitor.evals import gate
from hall_monitor.governance.approval import FinalDecision
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog, ChainHead
from hall_monitor.governance.controls import REGISTER, load_register
from hall_monitor.kill_switch import STOPS_FILENAME, load_stops
from hall_monitor.models import AgentCall
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
from hall_monitor.tools.server import CLAIMS_HISTORY, POLICY_LOOKUP

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


# A damaged file, or an entry that no longer parses: the audit view says which.
_UNREADABLE = (sqlite3.DatabaseError, ValueError, TypeError)

# IDs the panel links to and saves pages for. Anything else is shown as text, never in a path.
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def linkable(identifier: object) -> bool:
    return isinstance(identifier, str) and SAFE_ID.fullmatch(identifier) is not None


def _entries(src: Sources, claim_id: str | None = None) -> tuple[list[dict], str | None]:
    try:
        with _audit(src) as log:
            return (log.entries(claim_id) if log is not None else []), None
    except _UNREADABLE as exc:
        return [], f"the audit log can't be read ({type(exc).__name__}); see Audit integrity"


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


# --- What the panel shows of each entry -------------------------------------------------------
#
# The log holds IDs, hashes, decision fields and costs, but a few fields carry what the model
# wrote: tool arguments and tool errors, a policy number a code correction replaced, revision
# values. The panel shows an allow-list of fields per event, and a reason only when it matches
# one of the code's own templates. A template added later is withheld until it is listed here:
# the panel fails closed.

WITHHELD = "withheld"
_N = r"-?[\d,]+(?:\.\d+)?"
_CODES = r"[a-z_]+(?:, [a-z_]+)*"
_STOPS = (
    r"agent refused or returned no usable output: [a-z_]+ (?:declined claim \S+|was still "
    r"calling tools after \d+ rounds|returned no complete output for \S+)",
    r"[A-Za-z]+ from the model call",
    r"hall pass refused: agent '[a-z_]+' (?:has no hall pass|is (?:planned|retired), not active|"
    r"is stopped \(stop-\d+ by [\w-]+\)|(?:manifest|prompt) [0-9a-f]+ is not the approved "
    r"version|may not see [a-z, ]+ data)",
    r"hall pass refused: [\w .()'-]+; every agent is refused",
    r"no policy record found (?:for the policy number on the claim|after the critic's correction)",
    r"unexpected [A-Za-z]+",
)
_COST_UNKNOWN = r"(?:; cost unknown: no price for model '[\w.-]+' in PRICES_PER_MTOK)?"
_STOP = rf"(?:{'|'.join(_STOPS)}){_COST_UNKNOWN}"
_REASONS = (
    r"claim is in [A-Z]{3}, not USD: amounts can't be compared",
    r"no policy record to check the claim against",
    r"claimant is not the insured named on the policy",
    r"no incident date to check against the coverage period",
    r"incident date is outside the coverage period",
    r"no claim text to check the claimed amounts against",
    rf"claimed amounts not in the claim documents in USD: {_N}(?:, {_N})*",
    rf"claimed items \({_N}\) don't add up to the {_N} claimed",
    r"recommended [a-z ]+",
    r"nothing covered \(a denial\)",
    rf"payout {_N} exceeds the {_N} claimed",
    rf"payout {_N} above {_N} threshold",
    rf"fraud flags: {_CODES}",
    r"(?:low|medium|high) risk",
    r"critic still disagrees after revisions",
    r"critic changed [a-z_.]+(?:, [a-z_.]+)*",
    rf"claim text addresses the AI: {_CODES}",
    rf"pipeline stopped: {_STOP}",
)
_REJECTIONS = (
    r"the decision names an adjuster who isn't registered",
    r"the decision isn't signed with [\w-]+'s key",
    r"evidence hash doesn't match the current evidence pack",
    r"this claim (?:is in [A-Z]{3}|has no amount extracted), so its amount can't be checked "
    r"against USD limits; a senior adjuster must decide",
    rf"denying {_N} exceeds [\w-]+'s authority of {_N}; a senior adjuster must decide",
    r"a payment needs a payout above 0",
    rf"payout exceeds the {_N} claimed",
    rf"payout exceeds [\w-]+'s authority of {_N}; a senior adjuster must decide",
    r"paying a claim the AI never assessed for fraud needs a senior adjuster",
    r"paying a claim with a fraud flag or above-low risk needs a senior adjuster",
    r"[a-z_]+ can't carry a payout",
    r"overriding the AI or denying a claim needs a reason of at least \d+ characters",
    r"the decision isn't valid \([a-z_, ]+\)",
    r"the saved triage result isn't the one recorded at triage",
    r"the saved evidence pack (?:isn't the one recorded at triage|was changed after triage)",
    r"the evidence pack recorded at triage is no longer stored",
    r"evidence pack [0-9a-f]+ was changed after it was stored",
    r"the claim was already decided",
)
_REASON_RE = re.compile("|".join(_REASONS))
_STOP_RE = re.compile(_STOP)
_REJECTION_RE = re.compile("|".join(_REJECTIONS))
_CORRECTION_RE = re.compile(
    r"policy_number .* replaced by .*, the only valid policy number in the claim text"
)
TOOLS = frozenset({POLICY_LOOKUP, CLAIMS_HISTORY})


def _template(text: object, pattern: re.Pattern) -> str | None:
    if text is None:
        return None
    return text if isinstance(text, str) and pattern.fullmatch(text) else WITHHELD


def shown_reason(reason: object) -> str | None:
    """A routing reason as the code wrote it, or "withheld" if it isn't one of its templates."""
    return _template(reason, _REASON_RE)


def shown_stop(reason: object) -> str | None:
    return _template(reason, _STOP_RE)


def reason_kind(reason: str) -> str:
    """A routing reason without its case's figures, so reasons can be counted together."""
    for prefix, kind in (
        ("fraud flags:", "fraud flags"),
        ("claimed amounts not in the claim documents in USD", None),
        ("claimed items (", "claimed items don't add up to the total claimed"),
        ("claim text addresses the AI", None),
        ("pipeline stopped: agent refused or returned no usable output", None),
    ):
        if reason.startswith(prefix):
            return kind or prefix
    if re.fullmatch(rf"payout {_N} exceeds the {_N} claimed", reason):
        return "payout exceeds the amount claimed"
    return re.sub(rf"^payout {_N} above", "payout above", reason)


_SHOWN_FIELDS = {
    "claim_submitted": {
        "claim_sha256",
        "submitted_at",
        "attachments",
        "identifiers_redacted",
        "injection_signals",
    },
    "agent_call": set(AgentCall.model_fields),
    "tool_call": {"actor", "allowed", "result_sha256", "identifiers_redacted", "seconds"},
    "critic_review": set(),
    "revision": {"round"},
    "code_correction": set(),
    "triage_result": {
        "result_sha256",
        "route",
        "recommended_next_step",
        "covered_amount",
        "cost_usd",
        "models",
    },
    "evidence_pack": {"evidence_sha256", "result_sha256"},
    "decision_rejected": {"submitted_hmac"},
    "final_decision": (set(FinalDecision.model_fields) - {"adjuster_name", "reason"})
    | {"reason_hmac"},
}
# Revised fields whose values are typed by the code (enums, dates, amounts). A policy number is
# the policyholder's, and the model wrote it.
_REVISION_VALUES = {
    "claim_type",
    "incident_date",
    "total_claimed",
    "decision",
    "covered_amount",
    "level",
    "flags",
}


# A code-written string: an ID, hash, enum value, timestamp or "agent:model". Free text has spaces.
_CODE_TEXT = re.compile(r"[A-Za-z0-9_:.+@/-]{0,200}")


def _value(value: object, depth: int = 0) -> object:
    """A logged value as the code writes it (a number, a code, a list or map of them), or
    "withheld". The code writes only these; anything else means the entry was changed."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if _CODE_TEXT.fullmatch(value) else WITHHELD
    if depth == 0 and isinstance(value, list):
        return [_value(v, 1) for v in value]
    if depth == 0 and isinstance(value, dict):
        return {
            (k if _CODE_TEXT.fullmatch(str(k)) else WITHHELD): _value(v, 1)
            for k, v in value.items()
        }
    return WITHHELD


def _count(value: object) -> int:
    return len(value) if isinstance(value, list | dict) else int(value is not None)


def _reasons(value: object) -> list[str | None]:
    return [shown_reason(r) for r in value] if isinstance(value, list) else [WITHHELD]


def shown_payload(event: str, payload: object) -> dict:
    """What the panel shows of one entry's payload: its allow-listed fields, with free text
    replaced by "withheld"."""
    if not isinstance(payload, dict):
        return {"payload": WITHHELD}
    if event not in _SHOWN_FIELDS:
        return {"fields": f"{WITHHELD} ({len(payload)})"}
    shown = {k: _value(v) for k, v in payload.items() if k in _SHOWN_FIELDS[event]}
    hidden = len(payload) - len(shown)
    match event:
        case "tool_call":
            tool = payload.get("tool")
            shown["tool"] = tool if isinstance(tool, str) and tool in TOOLS else WITHHELD
            shown["arguments"] = f"{WITHHELD} ({_count(payload.get('arguments'))})"
            shown["error"] = WITHHELD if payload.get("error") else None
            hidden -= sum(k in payload for k in ("tool", "arguments", "error"))
        case "critic_review":
            issues = payload.get("issues")
            shown["issues"] = [
                {k: _value(i.get(k), 1) for k in ("stage", "problem_hmac")}
                for i in (issues if isinstance(issues, list) else [])
                if isinstance(i, dict)
            ]
            hidden -= "issues" in payload
        case "revision":
            changed = payload.get("changed") or {}
            shown["changed"] = {
                f: _value(v) if f in _REVISION_VALUES else [WITHHELD, WITHHELD]
                for f, v in (changed.items() if isinstance(changed, dict) else ())
                if f in _REVISION_VALUES or _CODE_TEXT.fullmatch(str(f))
            }
            hidden -= "changed" in payload
        case "code_correction":
            fixed = _CORRECTION_RE.fullmatch(str(payload.get("correction", "")))
            shown["correction"] = (
                "policy_number replaced by the only valid policy number in the claim text "
                "(numbers withheld)"
                if fixed
                else WITHHELD
            )
            hidden -= "correction" in payload
        case "triage_result":
            shown["routing_reasons"] = _reasons(payload.get("routing_reasons") or [])
            shown["stopped_reason"] = shown_stop(payload.get("stopped_reason"))
            hidden -= sum(k in payload for k in ("routing_reasons", "stopped_reason"))
        case "decision_rejected":
            shown["rejected_because"] = _template(payload.get("rejected_because"), _REJECTION_RE)
            hidden -= "rejected_because" in payload
        case "final_decision":
            shown["routing_reasons"] = _reasons(payload.get("routing_reasons") or [])
            if "signature" in payload:
                # With the public key it could confirm a guessed reason (HM-06 gap).
                shown["signature"] = "present (Ed25519)"
                hidden -= 1
    if hidden > 0:
        shown["other_fields"] = f"{WITHHELD} ({hidden})"
    return shown


# --- Cases -----------------------------------------------------------------------------------


def _number(value: object) -> float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _cases(entries: list[dict]) -> dict[str, dict]:
    """Each claim's state as the audit log records it. The log, not the checkpoints, decides,
    and the result that stands is the one the evidence pack records, as the workflow reads it."""
    cases: dict[str, dict] = {}
    results: dict[str, list[tuple[int, dict]]] = {}
    packs: dict[str, tuple[int, object]] = {}
    for e in entries:
        claim_id = e["claim_id"]
        case = cases.setdefault(
            claim_id,
            {
                "claim_id": claim_id,
                "linkable": linkable(claim_id),
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
        event = e["event"]
        shown = shown_payload(event, e["payload"])
        if event == "claim_submitted":
            case["submitted_at"] = case["submitted_at"] or e["recorded_at"]
        elif event == "triage_result":
            results.setdefault(claim_id, []).append((e["seq"], shown))
            case["cost_usd"] += _number(shown.get("cost_usd")) or 0.0  # every run cost money
        elif event == "evidence_pack" and claim_id not in packs:  # once per claim
            case["evidence"] = True
            packs[claim_id] = (e["seq"], shown.get("result_sha256"))
        elif event == "decision_rejected":
            case["rejected_decisions"] += 1
        elif event == "final_decision" and case["decided_by"] is None:  # the first one stands
            decided_by = shown.get("decided_by")
            case["decided_by"] = decided_by if isinstance(decided_by, str) else WITHHELD
            case["next_step"] = shown.get("next_step")
            case["payout_usd"] = _number(shown.get("payout_usd"))
            case["overrode_ai"] = shown.get("overrode_ai") is True
    for claim_id, case in cases.items():
        runs = results.get(claim_id, [])
        pack_seq, pack_result = packs.get(claim_id, (None, None))
        standing = next((r for _, r in runs if r.get("result_sha256") == pack_result), None)
        if standing is None and runs:
            # As AuditLog.result_hash: the latest result before the pack, or the latest.
            before = [r for seq, r in runs if pack_seq is None or seq < pack_seq]
            standing = (before or [runs[-1][1]])[-1]
        if standing is not None:
            route = standing.get("route")
            case["route"] = route if route in ("auto", "adjuster") else None
            case["routing_reasons"] = standing["routing_reasons"]
            case["recommended_next_step"] = standing.get("recommended_next_step")
            case["covered_amount"] = _number(standing.get("covered_amount"))
            case["stopped_reason"] = standing["stopped_reason"]
        if case["decided_by"]:
            case["state"] = "decided"
        elif case["evidence"] and case["route"] == "adjuster":
            case["state"] = "waiting for a person"
        elif case["evidence"]:
            # Triage finished but no decision was recorded: the workflow won't decide it again,
            # so a person has to look (workflow.status says needs_a_person or in_progress).
            case["state"] = (
                "routed automatically, no decision recorded"
                if case["route"] == "auto"
                else "triaged, no decision recorded"
            )
        else:
            case["state"] = "in triage, or stopped before it finished"
    return cases


def cases(src: Sources) -> list[dict]:
    entries, _ = _entries(src)
    return sorted(_cases(entries).values(), key=lambda c: c["claim_id"])


def review_queue(src: Sources) -> list[dict]:
    """Claims the routing rule sent to a person that nobody has decided, as the audit log
    records them. Deciding stays in the review CLI, where the adjuster sees the evidence and
    signs; it also checks the claim's saved state, so it can refuse one listed here."""
    return [c for c in cases(src) if c["state"] == "waiting for a person"]


def case_trace(src: Sources, claim_id: str) -> dict | None:
    """Every audit entry for one claim, in order, each with what the panel shows of it."""
    entries, problem = _entries(src, claim_id)
    if problem:
        return {"claim_id": claim_id, "case": None, "entries": [], "problem": problem}
    if not entries:
        return None
    shown = [
        {k: e[k] for k in ("seq", "recorded_at", "event", "actor", "entry_hash")}
        | {"payload": shown_payload(e["event"], e["payload"])}
        for e in entries
    ]
    return {
        "claim_id": claim_id,
        "case": _cases(entries)[claim_id],
        "entries": shown,
        "problem": problem,
    }


# --- Overview --------------------------------------------------------------------------------


def overview(src: Sources) -> dict:
    """Per workflow: cases, the share sent to a person, overrides, stopped cases, cost; and any
    agent that can't run, and whether the audit chain verifies."""
    entries, audit_problem = _entries(src)
    all_cases = sorted(_cases(entries).values(), key=lambda c: c["claim_id"])
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
        "no_decision_recorded": sum(c["state"].endswith("no decision recorded") for c in all_cases),
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
        "audit_problem": audit_problem,
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
        try:
            result = log.verify(expect=anchor)
        except sqlite3.DatabaseError as exc:
            return {
                "exists": True,
                "ok": False,
                "entries": 0,
                "head": None,
                "anchor": None if anchor is None else vars(anchor),
                "problems": [f"the file can't be read as an audit log ({type(exc).__name__})"],
                "evidence_erased": [],
            }
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
    try:
        approvals = load_approvals(src.registry_dir / APPROVALS_FILENAME)
        ok, results = gate.run_gate(src.gate_config, approvals)
        config = yaml.safe_load(src.gate_config.read_text())
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        # The gate would fail in CI too.
        problem = f"the gate can't run on these files ({type(exc).__name__})"
        return {"passed": False, "stale_results": None, "checks": [], "problem": problem}
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
    return {
        "passed": ok,
        "stale_results": config["stale_results"],
        "checks": checks,
        "problem": None,
    }


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
