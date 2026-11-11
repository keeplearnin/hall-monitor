"""Eval gate: fail when a published result is below its threshold, flag stale results.

    uv run python -m hall_monitor.evals.gate

Reads evals/gate.yaml. Uses published results only, so it makes no model calls and runs in CI.
A result is stale when the agent versions it was scored on differ from the approved ones in
registry/approvals.yaml; gate.yaml says whether that warns or fails.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hall_monitor.data import load_ground_truth
from hall_monitor.registry import load_approvals

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "evals" / "gate.yaml"


@dataclass
class CheckResult:
    name: str
    state: str  # "pass", "fail", "missing", "stale"
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # The values the thresholds were checked against, and the run's meta (when, which agents).
    metrics: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


def unsafe_routes(data: dict) -> int:
    """Runs where the answer key needed a person but the claim was routed automatically."""
    count = 0
    for run in data.get("runs", []):
        result = run.get("result")
        if not result or run.get("score", {}).get("error"):
            continue
        # A variant's ID is the claim's plus "-case" (older results used "+" or "~").
        claim_id = result["claim_id"].split("+")[0].split("~")[0].split("-")[0]
        if (
            load_ground_truth(claim_id).route.value == "adjuster"
            and result["routing"]["route"] == "auto"
        ):
            count += 1
    return count


def stale_agents(data: dict, approvals: dict) -> list[str]:
    """Agents whose scored manifest digest or prompt template isn't the approved one. Empty if
    not recorded. Results from before prompt hashes were recorded are checked on the digest."""
    stale = []
    for agent in data.get("meta", {}).get("agents", []):
        approved = approvals.get(agent["agent_id"], {})
        prompt = agent.get("prompt_template_sha256")
        if approved.get("manifest_digest") != agent.get("manifest_digest") or (
            prompt is not None and approved.get("prompt_sha256") != prompt
        ):
            stale.append(f"{agent['agent_id']} {agent.get('agent_version', '?')}")
    return stale


def check(spec: dict, approvals: dict, stale_policy: str) -> CheckResult:
    path = ROOT / spec["results"]
    if not path.exists():
        state = "fail" if spec.get("required") else "missing"
        return CheckResult(spec["name"], state, failures=[f"{spec['results']} not found"])
    data = json.loads(path.read_text())
    metrics = dict(data.get("summary", {}))
    if "unsafe_routes" in spec.get("max", {}):
        metrics["unsafe_routes"] = unsafe_routes(data)

    out = CheckResult(spec["name"], "pass", metrics=metrics, meta=data.get("meta", {}))
    if "mode" in spec and out.meta.get("mode") != spec["mode"]:
        # A scripted result can't stand in for a live one, or the other way round.
        out.failures.append(f"mode = {out.meta.get('mode')} (needs {spec['mode']})")
    for key, floor in spec.get("min", {}).items():
        value = metrics.get(key)
        if value is None or value < floor:
            out.failures.append(f"{key} = {value} (min {floor})")
    for key, ceiling in spec.get("max", {}).items():
        value = metrics.get(key)
        if value is None or value > ceiling:
            out.failures.append(f"{key} = {value} (max {ceiling})")

    stale = stale_agents(data, approvals)
    if stale:
        out.notes.append("scored on agent versions no longer approved: " + ", ".join(stale))
    elif not data.get("meta", {}).get("agents"):
        # Unknown versions are treated as stale: a result that can't show what it scored
        # mustn't pass a gate that requires current results.
        stale = ["unrecorded"]
        out.notes.append("result doesn't record agent versions, so it counts as stale")

    if out.failures:
        out.state = "fail"
    elif stale:
        out.state = "fail" if stale_policy == "fail" else "stale"
    return out


def run_gate(
    config_path: Path = CONFIG, approvals: dict | None = None
) -> tuple[bool, list[CheckResult]]:
    """approvals defaults to registry/approvals.yaml."""
    config = yaml.safe_load(config_path.read_text())
    approvals = load_approvals() if approvals is None else approvals
    results = [check(spec, approvals, config["stale_results"]) for spec in config["checks"]]
    return all(r.state != "fail" for r in results), results


def main() -> int:
    ok, results = run_gate()
    for r in results:
        print(f"[{r.state.upper():7}] {r.name}")
        for line in r.failures + r.notes:
            print(f"          {line}")
    print("Eval gate passed." if ok else "Eval gate FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
