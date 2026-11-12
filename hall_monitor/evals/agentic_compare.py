"""The agentic claims path against the old fixed path, offline (ADR 0028).

    uv run python -m hall_monitor.evals.agentic_compare             # prints the summary
    uv run python -m hall_monitor.evals.agentic_compare --publish   # also writes RESULTS_DIR

Every synthetic claim runs once on the fixed path and, on the agentic path, under each of a set
of supervisor policies, all scripted: the specialists answer from the answer key and no model
is called. The policies are the old order and the ways a supervisor could go wrong (finish at
once, skip a step, repeat a step, hand everything off, and noisy policies that take a random
action 30% of the time, seeded).

What it measures is code, not judgement. ``unsafe_auto`` counts claims a policy got through
automatically that the fixed path sent to a person; it must be zero for every policy, since
auto rests on the same routing rule plus the required-evidence check. ``agreement`` is the
share of claims routed as the fixed path routed them: 1.0 for the old order, and lower for a
policy that skips work, because skipping sends the claim to a person. Extra cost is the
supervisor's own calls at the fake client's fixed token counts, so only its relative size
means anything. How well a live supervisor chooses is not measured here: that needs a live
eval, which costs money and runs only when asked.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from hall_monitor.agentic import Limits
from hall_monitor.agents.supervisor import scripted_answer
from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.evals.meta import run_meta
from hall_monitor.models import (
    CoverageFinding,
    CriticReview,
    Recommendation,
    RiskAssessment,
    Route,
    SupervisorAction,
    SupervisorDecision,
    SupervisorReason,
)
from hall_monitor.pipeline import triage
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "evals" / "results" / "agentic-scripted"
MODE = "scripted-answer-key"
RANDOM_SEEDS = (1, 2, 3)  # noisy policies: the right step 70% of the time, else any action

Policy = Callable[[dict], SupervisorDecision]


def _decide(action: SupervisorAction, reason=SupervisorReason.UNCERTAIN) -> SupervisorDecision:
    return SupervisorDecision(action=action, reason=reason)


def _except(skipped: SupervisorAction) -> Policy:
    def answer(request: dict) -> SupervisorDecision:
        decision = scripted_answer(request)
        if decision.action is skipped:
            return _decide(SupervisorAction.FINISH, SupervisorReason.WORK_COMPLETE)
        return decision

    return answer


def _noisy(rng: random.Random, share: float = 0.3) -> Policy:
    """Mostly the right step; a share of the time any action at all, with any stages."""
    actions = list(SupervisorAction)
    stages_of = ["intake", "coverage_check", "risk_fraud"]

    def answer(request: dict) -> SupervisorDecision:
        if rng.random() >= share:
            return scripted_answer(request)
        action = rng.choice(actions)
        revise = action is SupervisorAction.REVISE
        stages = rng.sample(stages_of, rng.randint(1, 3)) if revise else []
        return SupervisorDecision(action=action, reason=SupervisorReason.UNCERTAIN, stages=stages)

    return answer


def policies() -> dict[str, Callable[[], Policy]]:
    """Each a factory, called once per claim."""
    A = SupervisorAction
    found: dict[str, Callable[[], Policy]] = {
        "old_order": lambda: scripted_answer,
        "finish_at_once": lambda: lambda request: _decide(A.FINISH),
        "skip_coverage": lambda: _except(A.RUN_COVERAGE_CHECK),
        "skip_risk": lambda: _except(A.RUN_RISK_FRAUD),
        "skip_critic": lambda: _except(A.RUN_CRITIC),
        "skip_recommendation": lambda: _except(A.RUN_RECOMMENDATION),
        "repeat_intake": lambda: lambda request: _decide(A.RUN_INTAKE),
        "hand_off": lambda: lambda request: _decide(A.HAND_OFF),
    }
    for seed in RANDOM_SEEDS:
        # One generator per policy, shared across claims, so each claim sees different noise.
        rng = random.Random(seed)
        found[f"noisy_{seed}"] = lambda rng=rng: _noisy(rng)
    return found


class _AnswerKey:
    """The specialists answer from the claim's answer key; the supervisor from the policy."""

    def __init__(self, claim_id: str, policy: Policy | None):
        truth = load_ground_truth(claim_id)
        self.policy = policy
        self.answers = {
            type(truth.extraction): truth.extraction,
            CoverageFinding: CoverageFinding(**truth.coverage.model_dump(exclude={"decision"})),
            RiskAssessment: RiskAssessment(**truth.risk.model_dump(), reasoning="answer key"),
            CriticReview: CriticReview(issues=[]),
            Recommendation: Recommendation(next_step=truth.next_step, reasons=["answer key"]),
        }

    def parse(self, **kwargs):
        kind = kwargs["output_format"]
        answer = self.policy(kwargs) if kind is SupervisorDecision else self.answers[kind]
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=answer,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


def _client(claim_id: str, policy: Policy | None = None) -> SimpleNamespace:
    return SimpleNamespace(messages=_AnswerKey(claim_id, policy))


def run(registry: Registry | None = None, limit: int | None = None) -> dict:
    registry = registry or Registry.from_dir()
    gateway = ToolGateway(registry)
    wording = load_policy_wording()
    claims = list(iter_claims())[:limit]
    fixed = {}
    for claim in claims:
        result = triage(
            claim,
            registry=registry,
            gateway=gateway,
            wording=wording,
            client=_client(claim.claim_id),
            agentic=False,
        )
        fixed[claim.claim_id] = result
    rows = {}
    for name, make in policies().items():
        routes, unsafe, agree, stops = Counter(), [], 0, Counter()
        cost = specialist_calls = supervisor_calls = 0.0
        for claim in claims:
            result = triage(
                claim,
                registry=registry,
                gateway=gateway,
                wording=wording,
                client=_client(claim.claim_id, make()),
                limits=Limits(),
            )
            base = fixed[claim.claim_id]
            routes[result.routing.route.value] += 1
            agree += result.routing.route is base.routing.route
            if result.routing.route is Route.AUTO and base.routing.route is not Route.AUTO:
                unsafe.append(claim.claim_id)
            if result.stopped_reason:
                stops[result.stopped_reason.split(":")[0]] += 1
            cost += result.cost_usd
            supervisor_calls += sum(c.agent_id == "claims_supervisor" for c in result.agent_calls)
            specialist_calls += sum(c.agent_id != "claims_supervisor" for c in result.agent_calls)
        n = len(claims)
        rows[name] = {
            "claims": n,
            "routes": dict(sorted(routes.items())),
            "agreement": round(agree / n, 4),
            "unsafe_auto": len(unsafe),
            "unsafe_claims": unsafe,
            "stops": dict(sorted(stops.items())),
            "mean_specialist_calls": round(specialist_calls / n, 2),
            "mean_supervisor_calls": round(supervisor_calls / n, 2),
            "mean_cost_usd": round(cost / n, 6),
        }
    fixed_rows = {
        "routes": dict(Counter(r.routing.route.value for r in fixed.values())),
        "mean_specialist_calls": round(
            sum(len(r.agent_calls) for r in fixed.values()) / len(fixed), 2
        ),
        "mean_cost_usd": round(sum(r.cost_usd for r in fixed.values()) / len(fixed), 6),
    }
    return {
        "meta": run_meta(registry, mode=MODE, claims=len(claims)),
        "fixed": fixed_rows,
        "policies": rows,
        "summary": {
            "unsafe_auto": sum(r["unsafe_auto"] for r in rows.values()),
            "old_order_agreement": rows["old_order"]["agreement"],
        },
    }


def report(result: dict) -> str:
    lines = [
        "# Agentic path against the fixed path (offline, scripted)",
        "",
        f"Run {result['meta']['run_at']} at {result['meta']['git_commit'][:12]}, "
        f"{result['meta']['claims']} synthetic claims. Specialists answer from the answer key; "
        "the supervisor is a scripted policy. No model was called, so this measures code's "
        "bounds, not a live supervisor's judgement.",
        "",
        f"Fixed path: routes {result['fixed']['routes']}, "
        f"{result['fixed']['mean_specialist_calls']} specialist calls and "
        f"{result['fixed']['mean_cost_usd']:.4f} USD per claim (fake token counts).",
        "",
        "| Policy | Agreement with fixed | Unsafe auto | Routes | Stops | Specialist calls | "
        "Supervisor calls | Cost per claim |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, row in result["policies"].items():
        lines.append(
            f"| {name} | {row['agreement']:.0%} | {row['unsafe_auto']} | {row['routes']} | "
            f"{row['stops'] or '-'} | {row['mean_specialist_calls']} | "
            f"{row['mean_supervisor_calls']} | {row['mean_cost_usd']:.4f} |"
        )
    lines += [
        "",
        "- **Unsafe auto:** claims this policy got through automatically that the fixed path "
        "sent to a person. Must be 0.",
        "- **Agreement:** claims routed as the fixed path routed them. A policy that skips or "
        "repeats work sends claims to a person, so it agrees less; that is the bound working.",
        "- **Cost:** at the fake client's fixed 1,000 input and 200 output tokens per call; only "
        "the relative cost of the supervisor's calls means anything.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.evals.agentic_compare")
    parser.add_argument("--publish", action="store_true", help=f"write {RESULTS_DIR}")
    args = parser.parse_args(argv)
    result = run()
    text = report(result)
    print(text)
    if args.publish:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        (RESULTS_DIR / "report.md").write_text(text)
    return 0 if result["summary"]["unsafe_auto"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
