"""The AI use inventory and a model card per agent, generated from the registry and results.

    uv run python -m hall_monitor.governance.model_cards   # regenerate docs/model-cards.md

Everything on a card is read from code, so it can't drift from what runs: the approved manifest
(owner, risk tier, model, tools, data classes), the approved prompt version, any live stop, and
which published results scored the agent, live or scripted, and whether on the version approved
now. The two hand-written parts are each agent's workflow and its limits (what the agent may do
alone and what code forces to a person), kept here in WORKFLOWS and LIMITS. A test fails if the
doc is stale or an agent has no limits written.
"""

from __future__ import annotations

import json
from pathlib import Path

from hall_monitor.approvals import AGENT_MODULES, prompt_sha256
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, Registry, load_approvals, load_owners

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "model-cards.md"
RESULTS = ROOT / "evals" / "results"

WORKFLOWS = {
    "claims_supervisor": "Claims triage (synthetic travel insurance)",
    "intake": "Claims triage (synthetic travel insurance)",
    "coverage_check": "Claims triage (synthetic travel insurance)",
    "risk_fraud": "Claims triage (synthetic travel insurance)",
    "critic": "Claims triage (synthetic travel insurance)",
    "recommendation": "Claims triage (synthetic travel insurance)",
    "complaint_supervisor": "Complaint desk (public CFPB complaints, never replies)",
    "complaint_triage": "Complaint desk (public CFPB complaints, never replies)",
    "leave_supervisor": "HR leave (synthetic FMLA requests; employment, EU AI Act Annex III)",
    "leave_intake": "HR leave (synthetic FMLA requests; employment, EU AI Act Annex III)",
    "leave_explainer": "HR leave (synthetic FMLA requests; employment, EU AI Act Annex III)",
}

# What each agent may do alone, and what code forces to a person regardless of its answer.
LIMITS = {
    "claims_supervisor": (
        "Chooses the next step from a closed list; sees codes and amounts only, no tools.",
        "Can't choose auto: code routes, and auto needs every required step on the latest "
        "inputs plus the routing rule (HM-04, HM-32). Budgets, refusals and the kill switch "
        "stop the claim for a person.",
    ),
    "intake": (
        "Extracts the claim's fields from the redacted claim.",
        "Code checks the policy exists, traces every amount to the documents and checks dates "
        "and the claimant against the policy record (HM-04, HM-08).",
    ),
    "coverage_check": (
        "Works out what the policy pays, reading the policy record through policy_lookup.",
        "A denial, a payout above 2,500 USD or above the amount claimed always goes to an "
        "adjuster (HM-04); the decision label comes from the amount, in code.",
    ),
    "risk_fraud": (
        "Flags fraud signals and a risk level, reading claims history through its tool.",
        "Any flag or risk above low goes to an adjuster (HM-04). A missed signal is a known "
        "limit code can't see.",
    ),
    "critic": (
        "Reviews intake, coverage and risk and sends work back, up to twice.",
        "A critic-driven change to a decision field, or disagreement left unresolved, sends the "
        "claim to an adjuster (HM-07).",
    ),
    "recommendation": (
        "Proposes pay, request information, investigate or deny from a structured summary.",
        "Only proposes: anything but a clean pay within bounds goes to an adjuster (HM-04).",
    ),
    "complaint_supervisor": (
        "Chooses triage, one re-check, finish or hand-off; sees codes only, no tools.",
        "Can only add review: code chooses the queue, every complaint goes to a person, and a "
        "complaint no agent read goes at high urgency (HM-27, HM-32).",
    ),
    "complaint_triage": (
        "Summarises the complaint and suggests category, urgency and concerns; drafts an "
        "acknowledgement.",
        "Never sends anything (HM-25); can raise urgency and add reasons, never lower or remove "
        "them; the draft is kept only if it promises nothing and holds no contact path.",
    ),
    "leave_supervisor": (
        "Chooses intake, one re-check before the rules, the rules, the note, finish or "
        "hand-off; sees codes only, no tools.",
        "Can't choose a route or role: the rules run on the first intake, early ends go to HR "
        "review at an HR director, and every request is signed by a person (HM-21, HM-23).",
    ),
    "leave_intake": (
        "Extracts dates, a reason category and yes-or-no facts; no free-text field.",
        "The rules in code decide eligibility, entitlement, the route and the role (HM-21); a "
        "person with the authority the rules name signs every decision (HM-23).",
    ),
    "leave_explainer": (
        "Writes an advisory note on the code's result for the person who signs.",
        "Can't change the route; a note that doesn't cover exactly the code's checks is "
        "dropped (HM-22).",
    ),
}


def _results() -> list[tuple[str, dict]]:
    found = []
    for path in sorted(RESULTS.glob("*/results.json")):
        meta = json.loads(path.read_text()).get("meta", {})
        found.append((path.parent.name, meta))
    return found


def _evidence(agent_id: str, approved: dict, results) -> list[str]:
    rows = []
    for name, meta in results:
        scored = next((a for a in meta.get("agents", []) if a["agent_id"] == agent_id), None)
        if scored is None:
            continue
        kind = "scripted" if (meta.get("mode") or "").startswith("scripted") else "live"
        current = scored.get("manifest_digest") == approved.get("manifest_digest") and (
            scored.get("prompt_template_sha256") in (None, approved.get("prompt_sha256"))
        )
        when = f"{scored.get('agent_version', '?')}, {'current' if current else 'older version'}"
        rows.append(f"[{name}](../evals/results/{name}/report.md) ({kind}, {when})")
    return rows


def render(registry: Registry, approvals: dict, owners: dict, results) -> str:
    agents = sorted(registry, key=lambda m: (WORKFLOWS.get(m.id, "~"), m.id))
    lines = [
        "# AI use inventory and model cards",
        "",
        "Generated by `uv run python -m hall_monitor.governance.model_cards` from the approved "
        "manifests (`registry/`), the approvals, the stop list and the published results. Do "
        "not edit by hand; a test fails if it is stale. **Evidence** says whether a published "
        "result scored the version approved now; *scripted* results use a stand-in model and "
        "measure code, not the model.",
        "",
        "## Inventory",
        "",
        "| Agent | Workflow | Owner | Risk tier | Model | Tools | Data classes | Live evidence "
        "on the approved version |",
        "|---|---|---|---|---|---|---|---|",
    ]
    cards = []
    for m in agents:
        approved = approvals.get(m.id, {})
        evidence = _evidence(m.id, approved, results)
        live_current = any("(live, " in e and ", current)" in e for e in evidence)
        owner = owners.get(m.owner, {})
        lines.append(
            f"| [`{m.id}`](#{m.id}) | {WORKFLOWS.get(m.id, 'unassigned')} | "
            f"{owner.get('role', m.owner)} (`{m.owner}`) | {m.risk_tier.value} | `{m.model}` | "
            f"{', '.join(f'`{t}`' for t in m.allowed_tools) or 'none'} | "
            f"{', '.join(c.value for c in m.data_classes) or 'none'} | "
            f"{'yes' if live_current else '**no**'} |"
        )
        module = AGENT_MODULES.get(m.id)
        alone, forced = LIMITS[m.id]
        cards += [
            f"### {m.id}",
            "",
            f"- **Name and purpose:** {m.name}. {m.purpose}",
            f"- **Workflow:** {WORKFLOWS.get(m.id, 'unassigned')}",
            f"- **Owner:** {owner.get('role', '?')} (`{m.owner}`), who can stop it at once "
            "(`python -m hall_monitor.kill_switch stop`)",
            f"- **Risk tier and status:** {m.risk_tier.value}, {m.status.value}",
            f"- **Model:** `{m.model}`"
            + (
                f"; economy `{m.economy_model}` for claims up to {m.economy_max_claim_usd:,.0f} "
                "USD, off unless model routing is on"
                if m.economy_model
                else ""
            ),
            f"- **Tools:** {', '.join(f'`{t}`' for t in m.allowed_tools) or 'none'}",
            f"- **Data classes it may receive:** "
            f"{', '.join(c.value for c in m.data_classes) or 'none'}",
            f"- **Approved version:** manifest {m.version} (`{m.digest()[:12]}`)"
            + (
                f", prompt `{module.PROMPT_VERSION}` (`{prompt_sha256(module)[:12]}`)"
                if module
                else ""
            ),
            f"- **May decide alone:** {alone}",
            f"- **Code forces to a person:** {forced}",
            f"- **Evidence:** {'; '.join(evidence) or '**none published**'}",
            "",
        ]
    return "\n".join(lines + ["", "## Model cards", "", *cards])


def current() -> str:
    registry = Registry.from_dir(DEFAULT_REGISTRY_DIR)
    owners = {o["id"]: o for o in load_owners()}
    return render(registry, load_approvals(), owners, _results())


if __name__ == "__main__":
    DOC.write_text(current())
