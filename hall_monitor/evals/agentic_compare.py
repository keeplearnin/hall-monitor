"""The agentic paths against the old fixed paths, offline (ADR 0028): claims and the desk.

    uv run python -m hall_monitor.evals.agentic_compare             # prints the summary
    uv run python -m hall_monitor.evals.agentic_compare --publish   # also writes RESULTS_DIR

Every synthetic claim runs once on the fixed path and, on the agentic path, under each of a set
of supervisor policies, all scripted: the specialists answer from the answer key and no model
is called. The policies are the old order and the ways a supervisor could go wrong (finish at
once, skip a step, repeat a step, hand everything off, and noisy policies that take a random
action 30% of the time, seeded). A second set runs with a critic that raises coverage and risk
once and then finds nothing, and a risk agent whose revised answer differs (the critic was right
about risk), to press on revisions: the old order, revising only part of what
was raised, finishing without the second review, and noisy policies.

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
from hall_monitor.agents.supervisor import scripted_answer, view_from_content
from hall_monitor.approvals import CLAIMS_AGENTS
from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
from hall_monitor.complaints.pipeline import code_reasons_for, triage_complaint
from hall_monitor.complaints.scripted import scripted_triage
from hall_monitor.complaints.supervisor import (
    DeskAction,
    DeskDecision,
    DeskReason,
    scripted_desk_answer,
)
from hall_monitor.complaints.supervisor import view_from_content as desk_view_from_content
from hall_monitor.complaints.triage_agent import Confidence, Urgency
from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.evals.meta import run_meta
from hall_monitor.models import (
    CoverageFinding,
    CriticIssue,
    CriticReview,
    Recommendation,
    ReviewedStage,
    RiskAssessment,
    RiskLevel,
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
DeskPolicy = Callable[[dict], DeskDecision]


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


def _revise_part(request: dict) -> SupervisorDecision:
    """The old order, but sending back only the first stage the critic raised."""
    decision = scripted_answer(request)
    if decision.action is SupervisorAction.REVISE:
        return decision.model_copy(update={"stages": decision.stages[:1]})
    return decision


def _no_rereview(request: dict) -> SupervisorDecision:
    """The old order, but finishing where it would ask the critic again after a revision."""
    decision = scripted_answer(request)
    view = view_from_content(request["messages"][0]["content"])
    if decision.action is SupervisorAction.RUN_CRITIC and view.revisions_used:
        return _decide(SupervisorAction.FINISH, SupervisorReason.WORK_COMPLETE)
    return decision


def raising_critic_policies() -> dict[str, Callable[[], Policy]]:
    """Run with a critic that raises coverage and risk once, then finds nothing: the revision
    lever (review 22 found a partial revision got such a claim through automatically)."""
    found: dict[str, Callable[[], Policy]] = {
        "old_order": lambda: scripted_answer,
        "revise_part": lambda: _revise_part,
        "no_rereview": lambda: _no_rereview,
    }
    for seed in RANDOM_SEEDS:
        rng = random.Random(100 + seed)
        found[f"noisy_{seed}"] = lambda rng=rng: _noisy(rng)
    return found


CRITICS = {
    "clean": [],
    "raising": [ReviewedStage.COVERAGE_CHECK, ReviewedStage.RISK_FRAUD],
}


class _AnswerKey:
    """The specialists answer from the claim's answer key; the supervisor from the policy."""

    def __init__(self, claim_id: str, policy: Policy | None, critic: str = "clean"):
        truth = load_ground_truth(claim_id)
        self.policy = policy
        raised = [CriticIssue(stage=s, problem="p", evidence="e") for s in CRITICS[critic]]
        # The critic's first review raises these stages; every later one finds nothing.
        self.reviews = [CriticReview(issues=raised)] if raised else []
        self.raising = bool(raised)
        self.answers = {
            type(truth.extraction): truth.extraction,
            CoverageFinding: CoverageFinding(**truth.coverage.model_dump(exclude={"decision"})),
            RiskAssessment: RiskAssessment(**truth.risk.model_dump(), reasoning="answer key"),
            CriticReview: CriticReview(issues=[]),
            Recommendation: Recommendation(next_step=truth.next_step, reasons=["answer key"]),
        }

    def parse(self, **kwargs):
        kind = kwargs["output_format"]
        if kind is SupervisorDecision:
            answer = self.policy(kwargs)
        elif kind is CriticReview and self.reviews:
            answer = self.reviews.pop(0)
        elif kind is RiskAssessment and self.raising and _revising(kwargs):
            # The critic was right about risk: the revised assessment differs, so a revision
            # that leaves risk out would miss a change the fixed path records.
            answer = self.answers[kind].model_copy(update={"level": RiskLevel.MEDIUM})
        else:
            answer = self.answers[kind]
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=answer,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


def _revising(request: dict) -> bool:
    return "<reviewer_feedback>" in request["messages"][0]["content"]


def _client(claim_id: str, policy: Policy | None = None, critic: str = "clean"):
    return SimpleNamespace(messages=_AnswerKey(claim_id, policy, critic))


def run(
    registry: Registry | None = None, limit: int | None = None, desk_limit: int | None = None
) -> dict:
    registry = registry or Registry.from_dir()
    gateway = ToolGateway(registry)
    wording = load_policy_wording()
    claims = list(iter_claims())[:limit]
    fixed: dict[str, dict] = {}
    for critic in CRITICS:
        fixed[critic] = {
            claim.claim_id: triage(
                claim,
                registry=registry,
                gateway=gateway,
                wording=wording,
                client=_client(claim.claim_id, critic=critic),
                agentic=False,
            )
            for claim in claims
        }
    rows = {}
    runs = [("clean", name, make) for name, make in policies().items()]
    runs += [("raising", name, make) for name, make in raising_critic_policies().items()]
    for critic, name, make in runs:
        routes, unsafe, agree, stops = Counter(), [], 0, Counter()
        cost = specialist_calls = supervisor_calls = 0.0
        for claim in claims:
            result = triage(
                claim,
                registry=registry,
                gateway=gateway,
                wording=wording,
                client=_client(claim.claim_id, make(), critic),
                limits=Limits(),
            )
            base = fixed[critic][claim.claim_id]
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
        rows[name if critic == "clean" else f"{critic}_critic/{name}"] = {
            "critic": critic,
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
    clean = fixed["clean"]
    fixed_rows = {
        "routes": dict(Counter(r.routing.route.value for r in clean.values())),
        "mean_specialist_calls": round(
            sum(len(r.agent_calls) for r in clean.values()) / len(clean), 2
        ),
        "mean_cost_usd": round(sum(r.cost_usd for r in clean.values()) / len(clean), 6),
    }
    desk = run_desk(registry, desk_limit)
    return {
        # Every agent the run used, so a change to any of them makes the result stale.
        "meta": run_meta(
            registry,
            [*CLAIMS_AGENTS, "complaint_triage", "complaint_supervisor"],
            mode=MODE,
            claims=len(claims),
            complaints=desk["complaints"],
        ),
        "fixed": fixed_rows,
        "policies": rows,
        "desk": desk,
        "summary": {
            "unsafe_auto": sum(r["unsafe_auto"] for r in rows.values()),
            "desk_less_review": sum(r["less_review"] for r in desk["policies"].values()),
            "desk_old_order_agreement": desk["policies"]["old_order"]["agreement"],
            "old_order_agreement": min(
                rows["old_order"]["agreement"], rows["raising_critic/old_order"]["agreement"]
            ),
        },
    }


# --- The complaint desk ---------------------------------------------------------------------------


def _desk_noisy(rng: random.Random, share: float = 0.3) -> DeskPolicy:
    def answer(request: dict) -> DeskDecision:
        if rng.random() >= share:
            return scripted_desk_answer(request)
        return DeskDecision(action=rng.choice(list(DeskAction)), reason=DeskReason.UNCERTAIN)

    return answer


def _desk_recheck(request: dict) -> DeskDecision:
    view = desk_view_from_content(request["messages"][0]["content"])
    if len(view.triages) == 1:
        return DeskDecision(action=DeskAction.RECHECK_TRIAGE, reason=DeskReason.UNCERTAIN)
    return scripted_desk_answer(request)


def desk_policies() -> dict[str, tuple[Callable[[], DeskPolicy], bool]]:
    """Each a factory and whether the re-check's triage plays everything down (urgency low, no
    concerns): a re-check can't lower urgency or remove a reason."""
    found: dict[str, tuple[Callable[[], DeskPolicy], bool]] = {
        "old_order": (lambda: scripted_desk_answer, False),
        "finish_at_once": (
            lambda: lambda r: DeskDecision(action=DeskAction.FINISH, reason=DeskReason.UNCERTAIN),
            False,
        ),
        "hand_off": (
            lambda: lambda r: DeskDecision(action=DeskAction.HAND_OFF, reason=DeskReason.UNCERTAIN),
            False,
        ),
        "repeat_triage": (
            lambda: (
                lambda r: DeskDecision(action=DeskAction.RUN_TRIAGE, reason=DeskReason.NEEDS_TRIAGE)
            ),
            False,
        ),
        # An answer of the wrong type on the first turn: the loop fails before any triage
        # (review 23 found such a complaint kept the code's floor urgency).
        "fails_at_once": (lambda: lambda r: CriticReview(issues=[]), False),
        "recheck_agrees": (lambda: _desk_recheck, False),
        "recheck_plays_down": (lambda: _desk_recheck, True),
    }
    for seed in RANDOM_SEEDS:
        rng = random.Random(200 + seed)
        found[f"noisy_{seed}"] = (lambda rng=rng: _desk_noisy(rng), False)
    return found


class _DeskScripted:
    """The scripted triage stand-in (complaints/scripted.py); the desk supervisor from the
    policy. With play_down, every triage after the first answers low urgency and no concerns."""

    def __init__(self, policy: DeskPolicy | None, play_down: bool):
        self.policy, self.play_down, self.triages = policy, play_down, 0

    def parse(self, **kwargs):
        if kwargs["output_format"] is DeskDecision:
            answer = self.policy(kwargs)
        else:
            answer = scripted_triage(kwargs["messages"][0]["content"])
            self.triages += 1
            if self.play_down and self.triages > 1:
                answer = answer.model_copy(
                    update={"urgency": Urgency.LOW, "concerns": [], "confidence": Confidence.HIGH}
                )
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=answer,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


def run_desk(registry: Registry, limit: int | None = None) -> dict:
    """Every complaint in the pinned snapshot on the desk's fixed order and under each policy.
    less_review counts complaints a policy gave less review than the fixed order: the standard
    queue where the fixed order chose specialist review, a lower urgency, or one of the code's
    own reasons (CFPB's labels and tags, the injection flag) dropped. A reason only the triage
    agent gives can't appear when no triage ran; such a complaint goes to specialist review at
    high urgency instead. It must be zero."""
    complaints = load_pinned(PINNED_NAME)[:limit]

    def triage(c, policy=None, play_down=False, agentic=True):
        client = SimpleNamespace(messages=_DeskScripted(policy, play_down))
        return triage_complaint(c, registry=registry, client=client, agentic=agentic)

    fixed = {c.complaint_id: triage(c, agentic=False).route for c in complaints}
    rows = {}
    for name, (make, play_down) in desk_policies().items():
        less, agree, queues, extra = [], 0, Counter(), Counter()
        supervisor_calls = 0
        for c in complaints:
            result = triage(c, make(), play_down)
            base, route = fixed[c.complaint_id], result.route
            queues[route.queue] += 1
            agree += route.queue == base.queue and route.urgency is base.urgency
            code = code_reasons_for(c, result.injection_signals)
            if (
                (route.queue == "standard_queue" and base.queue != "standard_queue")
                or route.urgency.rank < base.urgency.rank
                or not (code & set(base.reasons)) <= set(route.reasons)
            ):
                less.append(c.complaint_id)
            extra.update(set(route.reasons) - set(base.reasons))
            supervisor_calls += sum(
                a.agent_id == "complaint_supervisor" for a in result.agent_calls
            )
        n = len(complaints)
        rows[name] = {
            "complaints": n,
            "queues": dict(sorted(queues.items())),
            "agreement": round(agree / n, 4),
            "less_review": len(less),
            "less_review_complaints": less[:20],
            "reasons_added": dict(sorted(extra.items())),
            "mean_supervisor_calls": round(supervisor_calls / n, 2),
        }
    return {
        "complaints": len(complaints),
        "fixed_queues": dict(Counter(r.queue for r in fixed.values())),
        "policies": rows,
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
        "## Complaint desk",
        "",
        f"{result['desk']['complaints']} complaints from the pinned CFPB snapshot, triaged by the "
        "scripted stand-in; fixed order queues "
        f"{result['desk']['fixed_queues']}. Every complaint goes to a person either way.",
        "",
        "| Policy | Same queue and urgency | Less review | Queues | Reasons added | "
        "Supervisor calls |",
        "|---|---|---|---|---|---|",
    ]
    for name, row in result["desk"]["policies"].items():
        lines.append(
            f"| {name} | {row['agreement']:.0%} | {row['less_review']} | {row['queues']} | "
            f"{row['reasons_added'] or '-'} | {row['mean_supervisor_calls']} |"
        )
    lines += [
        "",
        "- **Less review:** complaints this policy gave less review than the fixed order (the "
        "standard queue instead of specialist review, a lower urgency, or one of the code's own "
        "reasons dropped). "
        "Must be 0. `recheck_plays_down` answers every re-check with low urgency and no concerns.",
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
    clean = result["summary"]["unsafe_auto"] == 0 and result["summary"]["desk_less_review"] == 0
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
