"""Measure the critic directly: correct work with one planted error, and correct work with none.

    uv run python -m hall_monitor.evals.critic_seeded --out evals/results/critic-seeded

Each case gives the critic the answer key's extraction, coverage and risk for one claim. A
control case changes nothing, so any issue raised is a false alarm. Each seeded case plants one
error of a kind seen in earlier runs, and the critic catches it if it raises an issue against
the stage that holds the error. Only the critic is called, once per case.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.agents.base import ToolAccess
from hall_monitor.agents.critic import run_critic
from hall_monitor.data import (
    iter_claims,
    load_ground_truth,
    load_policy_wording,
)
from hall_monitor.gateway.cost import call_cost
from hall_monitor.models import (
    ClaimSubmission,
    CoverageAssessment,
    CoverageDecision,
    CoverageFinding,
    GroundTruth,
    IntakeExtraction,
    ReviewedStage,
    RiskAssessment,
    RiskFlag,
    RiskLevel,
)
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway


@dataclass
class Case:
    claim_id: str
    seed: str
    expected_stage: ReviewedStage | None  # None for a control
    extraction: IntakeExtraction
    coverage: CoverageAssessment
    risk: RiskAssessment


@dataclass
class CaseResult:
    claim_id: str
    seed: str
    expected_stage: str | None
    issue_stages: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    caught: bool | None = None  # seeded cases
    false_alarm: bool | None = None  # controls
    cost_usd: float = 0.0
    error: str | None = None


def _correct(truth: GroundTruth) -> tuple[IntakeExtraction, CoverageAssessment, RiskAssessment]:
    coverage = CoverageAssessment(**truth.coverage.model_dump())
    flags = truth.risk.flags
    reasoning = (
        "Flags: " + ", ".join(flags) + "."
        if flags
        else "No inconsistencies found between the narrative, the documents and the policy record."
    )
    risk = RiskAssessment(level=truth.risk.level, flags=flags, reasoning=reasoning)
    return truth.extraction, coverage, risk


def _amount_ignores_cap(truth: GroundTruth) -> Case | None:
    """The slip: reasoning names the cap or deduction, the amount pays the full claim."""
    if truth.coverage.decision is not CoverageDecision.PARTIALLY_COVERED:
        return None
    extraction, coverage, risk = _correct(truth)
    finding = CoverageFinding(
        applicable_clauses=coverage.applicable_clauses,
        covered_amount=extraction.total_claimed,
        reasoning=coverage.reasoning,
    )
    wrong = CoverageAssessment.from_finding(finding, extraction.total_claimed)
    return Case(
        truth.claim_id, "amount_ignores_cap", ReviewedStage.COVERAGE_CHECK, extraction, wrong, risk
    )


def _unsupported_flag(truth: GroundTruth) -> Case:
    extraction, coverage, risk = _correct(truth)
    flags = [*risk.flags, RiskFlag.AMOUNTS_INCONSISTENT]
    level = RiskLevel.HIGH if risk.level is RiskLevel.HIGH else RiskLevel.MEDIUM
    wrong = RiskAssessment(
        level=level,
        flags=list(dict.fromkeys(flags)),
        reasoning=risk.reasoning + " The amounts claimed don't match the documents.",
    )
    return Case(
        truth.claim_id, "unsupported_flag", ReviewedStage.RISK_FRAUD, extraction, coverage, wrong
    )


def _missing_date(truth: GroundTruth) -> Case:
    extraction, coverage, risk = _correct(truth)
    wrong = extraction.model_copy(update={"incident_date": None})
    return Case(
        truth.claim_id, "missing_incident_date", ReviewedStage.INTAKE, wrong, coverage, risk
    )


def _control(truth: GroundTruth) -> Case:
    return Case(truth.claim_id, "control", None, *_correct(truth))


SEEDS: list[Callable[[GroundTruth], Case | None]] = [
    _control,
    _amount_ignores_cap,
    _unsupported_flag,
    _missing_date,
]


def build_cases(truths: list[GroundTruth]) -> list[Case]:
    return [case for truth in truths for seed in SEEDS if (case := seed(truth)) is not None]


def run_case(case: Case, claim: ClaimSubmission, context: dict) -> CaseResult:
    result = CaseResult(
        case.claim_id, case.seed, case.expected_stage.value if case.expected_stage else None
    )
    try:
        run = run_critic(
            claim,
            case.extraction,
            case.coverage,
            case.risk,
            context["wording"],
            context["registry"],
            context["client"],
            tools=ToolAccess(context["gateway"]),
        )
    except Exception as exc:  # a failed case is reported, not hidden
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    result.cost_usd = call_cost(run.call)
    result.issues = [i.model_dump(mode="json") for i in run.output.issues]
    result.issue_stages = sorted({i.stage.value for i in run.output.issues})
    if case.expected_stage is None:
        result.false_alarm = bool(run.output.issues)
    else:
        result.caught = case.expected_stage.value in result.issue_stages
    return result


def summarize(results: list[CaseResult]) -> dict:
    summary: dict = {"cases": len(results), "errors": sum(r.error is not None for r in results)}
    for seed in dict.fromkeys(r.seed for r in results):
        rs = [r for r in results if r.seed == seed and r.error is None]
        if seed == "control":
            summary["control_false_alarm_rate"] = sum(r.false_alarm for r in rs) / len(rs)
        else:
            summary[f"{seed}_caught_rate"] = sum(r.caught for r in rs) / len(rs)
        summary[f"{seed}_cases"] = len(rs)
    ok = [r for r in results if r.error is None]
    summary["cost_per_review_usd"] = sum(r.cost_usd for r in ok) / len(ok) if ok else None
    return summary


def write_report(path: Path, meta: dict, summary: dict, results: list[CaseResult]) -> None:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.1%}"

    lines = [
        "# Critic eval: seeded errors",
        "",
        f"Run {meta['run_at']} on commit `{meta['git_commit'][:7]}`. {summary['cases']} cases, "
        f"{summary['errors']} errored. One critic call per case.",
        "",
        "| Case | Cases | Result |",
        "|---|---|---|",
        f"| Control (nothing wrong): false alarms | {summary.get('control_cases', 0)} "
        f"| {pct(summary.get('control_false_alarm_rate'))} |",
    ]
    for seed in ("amount_ignores_cap", "unsupported_flag", "missing_incident_date"):
        lines.append(
            f"| {seed.replace('_', ' ').capitalize()}: caught | {summary.get(f'{seed}_cases', 0)} "
            f"| {pct(summary.get(f'{seed}_caught_rate'))} |"
        )
    lines += [
        f"| Cost per review | | ${summary['cost_per_review_usd'] or 0:.4f} |",
        "",
        "## Misses and false alarms",
        "",
    ]
    for r in results:
        if r.error:
            lines.append(f"- {r.claim_id} {r.seed}: error {r.error}")
        elif r.caught is False or r.false_alarm:
            what = "false alarm" if r.false_alarm else "missed"
            detail = "; ".join(f"[{i['stage']}] {i['problem'][:160]}" for i in r.issues) or "none"
            lines.append(f"- {r.claim_id} {r.seed} ({what}). Issues raised: {detail}")
    lines += [
        "",
        "## Reproduce",
        "",
        "```bash",
        f"uv run python -m hall_monitor.evals.critic_seeded --out {path.parent}",
        "```",
        "",
    ]
    path.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hall_monitor.evals.critic_seeded")
    parser.add_argument("--out", type=Path, default=Path("evals/results/critic-seeded"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)

    load_dotenv()
    claims = {c.claim_id: c for c in iter_claims()}
    cases = build_cases([load_ground_truth(cid) for cid in claims])
    registry = Registry.from_dir()
    context = {
        "registry": registry,
        "gateway": ToolGateway(registry),
        "wording": load_policy_wording(),
        "client": anthropic.Anthropic(),
    }
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda c: run_case(c, claims[c.claim_id], context), cases))
    tracing.flush()

    def git(*a: str) -> str:
        return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()

    meta = {
        "run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
    }
    summary = summarize(results)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps(
            {"meta": meta, "summary": summary, "cases": [asdict(r) for r in results]}, indent=2
        )
        + "\n"
    )
    write_report(args.out / "report.md", meta, summary, results)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
