"""Run every synthetic claim through the pipeline, score it, and write the results.

    uv run python -m hall_monitor.evals.baseline --trials 3 --out evals/results/baseline

Writes results.json (every run, every score, every hall pass) and report.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from hall_monitor import tracing
from hall_monitor.data import (
    iter_claims,
    load_ground_truth,
    load_policy_records,
    load_policy_wording,
)
from hall_monitor.evals import scoring
from hall_monitor.evals.cost import call_cost
from hall_monitor.governance import ADJUSTER_PAYOUT_THRESHOLD_USD
from hall_monitor.models import ClaimSubmission, TriageResult
from hall_monitor.pipeline import MAX_REVISIONS, triage
from hall_monitor.registry import Registry


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def run_one(
    claim: ClaimSubmission, trial: int, context: dict
) -> tuple[scoring.RunScore, TriageResult | None]:
    try:
        result = triage(
            claim,
            registry=context["registry"],
            policies=context["policies"],
            wording=context["wording"],
            client=context["client"],
            critic=context["critic"],
        )
    except Exception as exc:  # a failed run is scored, not hidden
        return scoring.failed_run(claim.claim_id, trial, f"{type(exc).__name__}: {exc}"), None
    score = scoring.score_run(
        result,
        context["truths"][claim.claim_id],
        claim,
        context["wording"],
        trial=trial,
        cost_usd=sum(call_cost(c) for c in result.agent_calls),
    )
    return score, result


def write_report(path: Path, meta: dict, summary: dict, runs: list[dict], truths: dict) -> None:
    lines = [
        f"# Eval: {'pipeline with critic' if meta['critic'] else 'single-pass pipeline'}",
        "",
        f"Run {meta['run_at']} on commit `{meta['git_commit'][:7]}`"
        f"{' (uncommitted changes)' if meta['git_dirty'] else ''}. "
        f"{meta['claims']} synthetic claims x {meta['trials']} trials = {summary['runs']} runs, "
        f"{summary['errors']} errored.",
        "",
        "Pipeline: intake, policy lookup, coverage check, risk and fraud, "
        + (f"critic (up to {MAX_REVISIONS} revisions), " if meta["critic"] else "no critic, ")
        + "recommendation, then the routing rule. No MCP tools (milestone 4).",
        "",
        "## Headline",
        "",
        "| Extraction accuracy | Coverage-reasoning accuracy | Hallucination rate "
        "| Cost per claim | Human override rate |",
        "|---|---|---|---|---|",
        f"| {_pct(summary['extraction_accuracy'])} "
        f"| {_pct(summary['coverage_reasoning_accuracy'])} "
        f"| {_pct(summary['hallucination_rate'])} | ${summary['cost_per_claim_usd'] or 0:.4f} "
        "| n/a (no adjusters yet) |",
        "",
        "## Detail",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key in (
        "coverage_decision_accuracy",
        "covered_amount_accuracy",
        "clause_recall",
        "risk_level_accuracy",
        "flag_precision",
        "flag_recall",
        "next_step_accuracy",
        "route_accuracy",
    ):
        lines.append(f"| {key.replace('_', ' ').capitalize()} | {_pct(summary[key])} |")
    lines.append(
        f"| Seconds per claim (sum of agent calls) | {summary['seconds_per_claim'] or 0:.1f} |",
        f"| Runs revised after critic review | {_pct(summary['revised_rate'])} |",
        f"| Runs where the critic still disagreed | {_pct(summary['critic_unresolved_rate'])} |",
        f"| Runs stopped (policy not found) | {_pct(summary['stopped_rate'])} |",
    )

    lines += [
        "",
        "## By claim",
        "",
        "Counts are correct runs out of trials.",
        "",
        "| Claim | Scenario | Coverage | Next step | Route | Expected flags | Flags raised |",
        "|---|---|---|---|---|---|---|",
    ]
    for claim_id in sorted(truths):
        claim_runs = [r for r in runs if r["score"]["claim_id"] == claim_id]
        n = len(claim_runs)
        raised = Counter(
            f
            for r in claim_runs
            if r["result"]
            for f in (r["result"]["risk"] or {"flags": []})["flags"]
        )
        raised_text = ", ".join(f"{f} ({c})" for f, c in sorted(raised.items())) or "none"
        truth = truths[claim_id]
        lines.append(
            f"| {claim_id} | {truth.scenario} "
            f"| {sum(r['score']['coverage_correct'] for r in claim_runs)}/{n} "
            f"| {sum(r['score']['next_step'] for r in claim_runs)}/{n} "
            f"| {sum(r['score']['route'] for r in claim_runs)}/{n} "
            f"| {', '.join(truth.risk.flags) or 'none'} | {raised_text} |"
        )

    lines += ["", "## Extraction by field", "", "| Field | Accuracy |", "|---|---|"]
    for name in scoring.EXTRACTION_FIELDS:
        values = [r["score"]["extraction_fields"][name] for r in runs]
        lines.append(f"| {name} | {_pct(sum(values) / len(values))} |")

    unsupported = [
        f"{r['score']['claim_id']} trial {r['score']['trial']}: {u}"
        for r in runs
        for u in r["score"]["unsupported"]
    ]
    errors = [
        f"{r['score']['claim_id']} trial {r['score']['trial']}: {r['score']['error']}"
        for r in runs
        if r["score"]["error"]
    ]
    lines += ["", "## Unsupported values", ""]
    lines += [f"- {u}" for u in unsupported] or ["None."]
    lines += ["", "## Errors", ""]
    lines += [f"- {e}" for e in errors] or ["None."]

    lines += [
        "",
        "## Hall passes",
        "",
        "| Agent | Version | Model | Prompt | Manifest digest |",
        "|---|---|---|---|---|",
    ]
    for agent in meta["agents"]:
        lines.append(
            f"| {agent['agent_id']} | {agent['agent_version']} | {agent['model']} "
            f"| {agent['prompt_version']} | `{agent['manifest_digest'][:12]}` |"
        )

    lines += [
        "",
        "## Definitions",
        "",
        "- **Extraction accuracy:** share of extraction fields matching ground truth. Claimed "
        "items compare amounts only; a location matches if it names one of the expected "
        "place's words.",
        "- **Coverage-reasoning accuracy:** share of runs where the coverage decision is right, "
        "the covered amount is within 1 USD and every deciding clause is cited.",
        "- **Hallucination rate:** share of checked values not traceable to the source: an "
        "extracted name, policy number, location or amount missing from the claim, a document "
        "that wasn't attached, or a clause that isn't in the policy wording.",
        "- **Cost per claim:** list-price token cost of all four agent calls, thinking included.",
        "- **Route:** adjuster for denials, investigations, any fraud flag, or payouts above "
        f"USD {ADJUSTER_PAYOUT_THRESHOLD_USD:,}; otherwise automatic.",
        "- A run that errors scores zero on every metric except cost and time.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python -m hall_monitor.evals.baseline "
        f"--trials {meta['trials']} --out {path.parent}"
        + ("" if meta["critic"] else " --no-critic"),
        "```",
        "",
    ]
    path.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hall_monitor.evals.baseline")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("evals/results/baseline"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--no-critic", action="store_true", help="Skip the critic review loop.")
    args = parser.parse_args(argv)

    load_dotenv()
    claims = list(iter_claims())
    context = {
        "registry": Registry.from_dir(),
        "policies": load_policy_records(),
        "wording": load_policy_wording(),
        "client": anthropic.Anthropic(),
        "truths": {c.claim_id: load_ground_truth(c.claim_id) for c in claims},
        "critic": not args.no_critic,
    }
    jobs = [(claim, trial) for trial in range(1, args.trials + 1) for claim in claims]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outcomes = list(pool.map(lambda job: run_one(*job, context), jobs))
    tracing.flush()

    runs = [
        {"score": asdict(score), "result": result.model_dump(mode="json") if result else None}
        for score, result in outcomes
    ]
    summary = scoring.summarize([score for score, _ in outcomes])
    # The run that used the most agents lists every hall pass, critic included.
    first = max(
        (result for _, result in outcomes if result),
        key=lambda r: len({c.agent_id for c in r.agent_calls}),
        default=None,
    )
    meta = {
        "run_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "claims": len(claims),
        "trials": args.trials,
        "critic": not args.no_critic,
        "adjuster_payout_threshold_usd": ADJUSTER_PAYOUT_THRESHOLD_USD,
        # One row per agent: revised runs call some agents more than once.
        "agents": list(
            {
                c.agent_id: c.model_dump(exclude={"input_tokens", "output_tokens", "seconds"})
                for c in first.agent_calls
            }.values()
        )
        if first
        else [],
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "runs": runs}, indent=2) + "\n"
    )
    write_report(args.out / "report.md", meta, summary, runs, context["truths"])
    print(json.dumps(summary, indent=2))
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
