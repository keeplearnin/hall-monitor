"""Failure drills: many claims at once, with failures injected on purpose (HM-31).

    uv run python -m hall_monitor.drills                 # writes var/drills/
    uv run python -m hall_monitor.drills --rounds 3 --workers 8 --cap-usd 2.00

Runs the synthetic claims through the real claims workflow, several at a time on threads, into
its own registry copy, audit log and checkpoints. Every control runs as it does for real; only
the model is scripted (each agent answers with the claim's answer key), so nothing calls the API
and the run says nothing about how well the agents work.

Failures are injected on a plan fixed by a seed:

- per case: a wrong answer code can check (coverage pays more than was claimed), an answer of
  the wrong type, no answer, a truncated answer, an API connection error, an API timeout, a
  crash after the evidence pack is logged but before the checkpoint is saved, and the same claim
  submitted twice at once;
- for the batch: an owner stops the risk agent partway through (written to the drill's registry
  copy, never the shipped one), and a cost cap that refuses to start a case once what is spent
  and reserved would pass it.

Then code checks the outcome, reading the audit log rather than trusting the runner: every
failed, crashed or stopped case that started ends with a person; clean cases that finished
before the stop were handled normally; no case is left with no one; no case has two evidence
packs or two decisions (a duplicate submission may still run triage twice); every injected
failure shows in the log by its own reason; the log verifies; and the cost the log records is
under the cap. A case the cap refuses is logged but never enters the workflow, so it isn't
queued for anyone. Faults the stop reached first are listed as masked. Tracing is off while a
drill runs. The report counts each kind of failure by outcome and lists any problem. Exit code
0 when there are none, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import threading
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import anthropic
import yaml
from langgraph.checkpoint.sqlite import SqliteSaver

from hall_monitor.agents.supervisor import scripted_answer
from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.governance.audit import AuditLog
from hall_monitor.kill_switch import STOPS_FILENAME, add_stop
from hall_monitor.models import (
    ClaimSubmission,
    CoverageFinding,
    CriticReview,
    Recommendation,
    RiskAssessment,
)
from hall_monitor.registry import APPROVALS_FILENAME, DEFAULT_REGISTRY_DIR, Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import ClaimWorkflow, build_graph

DEFAULT_OUT = DEFAULT_REGISTRY_DIR.parent / "var" / "drills"
MARKER = ".drill"  # only a directory holding this is ever cleared

# Faults that bend one agent's answer; the agent is chosen by the plan.
MODEL_FAULTS = frozenset(
    {"wrong_answer", "wrong_type", "no_output", "truncated", "api_error", "timeout"}
)
CASE_FAULTS = (*sorted(MODEL_FAULTS), "crash", "duplicate")
WITH_A_PERSON = frozenset({"waiting_for_adjuster", "needs_a_person"})
# A case the cap refuses never enters the workflow: it has no checkpoint and waits for no one.
# It is logged as batch_budget_reached and is not counted as with a person.
NOT_STARTED = "not_started"
# What each model fault must leave as the logged stop reason when nothing else stopped the
# claim first. A wrong answer doesn't stop triage; the over-claim routing rule must catch it.
FAULT_REASONS = {
    "wrong_type": "agent refused or returned no usable output",
    "no_output": "agent refused or returned no usable output",
    "truncated": "agent refused or returned no usable output",
    "api_error": "APIConnectionError from the model call",
    "timeout": "APITimeoutError from the model call",
}
OVER_CLAIM_REASON = "exceeds the"  # governance/routing.py: payout above the amount claimed
STOPPED_REASON = "is stopped"  # registry.py: AgentStoppedError
TRACING_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")

STOPPED_AGENT, STOPPED_BY = "risk_fraud", "own-302"
# The most one triage run may cost. With the scripted token counts the dearest run in a full
# drill costs about $0.012 (every agent, the critic and a revision); twice that leaves room. A
# case that costs more is a problem. A live run would need a ceiling set from live costs.
CASE_CEILING_USD = 0.025

AGENT_KINDS = (
    "IntakeExtraction",
    "CoverageFinding",
    "RiskAssessment",
    "CriticReview",
    "Recommendation",
)


@dataclass(frozen=True)
class Case:
    case_id: str
    claim_id: str
    fault: str  # "none" or one of CASE_FAULTS
    agent: str | None  # the output type a model fault bends


def plan(*, rounds: int = 2, seed: int = 7, fault_share: float = 0.5) -> list[Case]:
    """Every synthetic claim `rounds` times, shuffled. fault_share of the cases get a fault,
    taking the faults in turn so each kind appears as evenly as the count allows; the rest get
    none. The same seed gives the same plan."""
    rng = random.Random(seed)
    slots = [(r, claim.claim_id) for r in range(1, rounds + 1) for claim in iter_claims()]
    faulted = round(len(slots) * fault_share)
    faults = [CASE_FAULTS[i % len(CASE_FAULTS)] for i in range(faulted)]
    faults += ["none"] * (len(slots) - faulted)
    rng.shuffle(faults)
    cases = []
    for (r, claim_id), fault in zip(slots, faults, strict=True):
        agent = rng.choice(AGENT_KINDS) if fault in MODEL_FAULTS else None
        if fault == "wrong_answer":
            agent = "CoverageFinding"
        cases.append(Case(f"{claim_id}-r{r}", claim_id, fault, agent))
    rng.shuffle(cases)
    return cases


class SimulatedCrash(Exception):
    """The process dies between logging the evidence pack and saving the checkpoint."""


class _CrashAfterPack(AuditLog):
    def append(self, claim_id, event, *args, **kwargs):
        head = super().append(claim_id, event, *args, **kwargs)
        if event == "evidence_pack":
            raise SimulatedCrash(claim_id)
        return head


class _Answers:
    """The scripted model for one case: the answer key, bent as the case's fault says."""

    def __init__(self, case: Case):
        truth = load_ground_truth(case.claim_id)
        self.case = case
        self.answers = {
            "IntakeExtraction": truth.extraction,
            "CoverageFinding": CoverageFinding(**truth.coverage.model_dump(exclude={"decision"})),
            "RiskAssessment": RiskAssessment(**truth.risk.model_dump(), reasoning="answer key"),
            "CriticReview": CriticReview(issues=[]),
            "Recommendation": Recommendation(next_step=truth.next_step, reasons=["answer key"]),
        }
        if case.fault == "wrong_answer":
            covered = round(truth.extraction.total_claimed + 0.01, 2)
            self.answers["CoverageFinding"] = self.answers["CoverageFinding"].model_copy(
                update={"covered_amount": covered}
            )

    def parse(self, **kwargs):
        kind = kwargs["output_format"].__name__
        if kind == "SupervisorDecision":  # the old fixed order, as a scripted supervisor
            answer = scripted_answer(kwargs)
        else:
            answer = self.answers[kind]
        stop_reason = "end_turn"
        if kind == self.case.agent:
            # No request object: nothing was sent, and only egress.py may use httpx (HM-25).
            request = None
            match self.case.fault:
                case "wrong_type":
                    answer = (
                        CriticReview(issues=[])
                        if kind == "Recommendation"
                        else self.answers["Recommendation"]
                    )
                case "no_output":
                    answer = None
                case "truncated":
                    stop_reason = "max_tokens"
                case "api_error":
                    raise anthropic.APIConnectionError(request=request)
                case "timeout":
                    raise anthropic.APITimeoutError(request=request)
        return SimpleNamespace(
            stop_reason=stop_reason,
            parsed_output=answer,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


class Budget:
    """A batch cost cap. A case reserves the ceiling before it starts and settles what it cost
    when it ends, so the cases running at once can't together pass the cap."""

    def __init__(self, cap_usd: float | None, ceiling_usd: float):
        self.cap, self.ceiling = cap_usd, ceiling_usd
        self.spent = self.reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, n: int = 1) -> bool:
        with self._lock:
            if self.cap is not None and self.spent + self.reserved + n * self.ceiling > self.cap:
                return False
            self.reserved += n * self.ceiling
            return True

    def release(self, n: int) -> None:
        """Give back a reservation whose cost couldn't be read; the check flags the case."""
        with self._lock:
            self.reserved -= n * self.ceiling

    def settle(self, n: int, cost: float) -> None:
        with self._lock:
            self.reserved -= n * self.ceiling
            self.spent += cost


@dataclass
class _Record:
    case: Case
    key_route: str
    started: bool = False
    after_stop: bool = False
    before_stop: bool = False
    errors: list[str] = field(default_factory=list)


def _registry_copy(out: Path) -> Path:
    """The shipped registry with an empty stop list (and so no cleared stops), for the drill's
    own stop. The shipped registry is never written."""
    registry = out / "registry"
    shutil.copytree(
        DEFAULT_REGISTRY_DIR, registry, ignore=shutil.ignore_patterns(".*", "*.schema.json")
    )
    (registry / STOPS_FILENAME).write_text("stops: []\n")
    approvals = yaml.safe_load((registry / APPROVALS_FILENAME).read_text())
    for entry in approvals["agents"].values():
        entry.pop("cleared_stops", None)
    (registry / APPROVALS_FILENAME).write_text(yaml.safe_dump(approvals, sort_keys=True))
    return registry


def _prepare(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        if not (out / MARKER).exists():
            raise ValueError(f"{out} isn't empty and isn't a drill directory; not clearing it")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("written by python -m hall_monitor.drills\n")


@contextmanager
def _workflow(out: Path, registry: Registry, client, *, crash: bool) -> Iterator[ClaimWorkflow]:
    audit = (_CrashAfterPack if crash else AuditLog)(out / "audit.sqlite")
    try:
        with SqliteSaver.from_conn_string(str(out / "checkpoints.sqlite")) as saver:
            app = build_graph(
                registry=registry,
                gateway=ToolGateway(registry),
                wording=load_policy_wording(),
                audit=audit,
                client=client,
            ).compile(checkpointer=saver)
            yield ClaimWorkflow(app, saver, audit)
    finally:
        audit.close()


@contextmanager
def _no_tracing() -> Iterator[None]:
    """Tracing off for the drill: it is scripted data and must not reach Langfuse even when keys
    are exported. The keys are put back afterwards. They are process-wide, so don't trace from
    other threads of the same process while a drill runs."""
    saved = {k: os.environ.pop(k) for k in TRACING_KEYS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _claim(case: Case) -> ClaimSubmission:
    original = next(c for c in iter_claims() if c.claim_id == case.claim_id)
    return original.model_copy(update={"claim_id": case.case_id})


# The workflow's own refusals of a claim it already has (workflow.py and the pipeline's
# once-only evidence pack).
DUPLICATE_REFUSALS = (
    "is already in the workflow",
    "already finished triage",
    "triage started but didn't finish",
    "was already triaged by another run",
)


def _refusal_kind(message: str) -> str:
    return next(m for m in DUPLICATE_REFUSALS if m in message)


def _submit(out: Path, registry_dir: Path, case: Case, record: _Record) -> None:
    registry = Registry.from_dir(registry_dir)  # reads the stop list on every agent check
    claim = _claim(case)
    client = SimpleNamespace(messages=_Answers(case))
    try:
        with _workflow(out, registry, client, crash=case.fault == "crash") as workflow:
            workflow.submit(claim)
    except SimulatedCrash:
        record.errors.append("crashed after the evidence pack was logged")
    except ValueError as exc:
        # Only a duplicate's second submission may be refused, and only with the workflow's own
        # refusals. Anything else (a pydantic ValidationError is a ValueError too, and repeats
        # input) is unexpected and recorded by its type, never its text.
        message = str(exc)
        if case.fault == "duplicate" and any(m in message for m in DUPLICATE_REFUSALS):
            record.errors.append(f"refused as a duplicate ({_refusal_kind(message)})")
        else:
            record.errors.append(f"unexpected {type(exc).__name__}")
    except Exception as exc:  # a drill finishes and reports; the check flags the case
        record.errors.append(f"unexpected {type(exc).__name__}")


def _logged_cost(out: Path, case_id: str) -> float:
    log = AuditLog(out / "audit.sqlite", read_only=True)
    try:
        return sum(
            e["payload"].get("cost_usd") or 0.0
            for e in log.entries(case_id)
            if e["event"] == "triage_result"
        )
    finally:
        log.close()


def run(
    out: Path = DEFAULT_OUT,
    *,
    rounds: int = 2,
    workers: int = 8,
    seed: int = 7,
    fault_share: float = 0.5,
    stop_after: int | None = None,
    cap_usd: float | None = None,
    ceiling_usd: float = CASE_CEILING_USD,
    limit: int | None = None,
) -> dict:
    """Run a drill under `out` (cleared first if it holds an earlier drill) and return the
    report. stop_after: cases begun before the owner's stop (default two thirds). cap_usd: the
    batch cost cap (none by default). limit: run only the first cases of the plan."""
    with _no_tracing():
        return _run(
            out, rounds, workers, seed, fault_share, stop_after, cap_usd, ceiling_usd, limit
        )


def _run(out, rounds, workers, seed, fault_share, stop_after, cap_usd, ceiling_usd, limit):
    _prepare(out)
    registry_dir = _registry_copy(out)
    cases = plan(rounds=rounds, seed=seed, fault_share=fault_share)[:limit]
    stop_after = len(cases) * 2 // 3 if stop_after is None else stop_after
    budget = Budget(cap_usd, ceiling_usd)
    records = {c.case_id: _Record(c, load_ground_truth(c.claim_id).route.value) for c in cases}
    lock = threading.Lock()
    progress = {"begun": 0, "stopped_at": None}

    def one(case: Case) -> None:
        record = records[case.case_id]
        n = 2 if case.fault == "duplicate" else 1
        with lock:
            if progress["begun"] == stop_after and progress["stopped_at"] is None:
                stop = add_stop(
                    registry_dir / STOPS_FILENAME,
                    agent=STOPPED_AGENT,
                    by=STOPPED_BY,
                    reason="Failure drill: the owner stops the risk agent mid-batch.",
                    now=datetime.now(UTC).replace(microsecond=0),
                )
                progress["stopped_at"] = stop.id
            progress["begun"] += 1
            record.after_stop = progress["stopped_at"] is not None
            if not budget.reserve(n):
                log = AuditLog(out / "audit.sqlite")
                try:
                    log.append(
                        case.case_id,
                        "batch_budget_reached",
                        "pipeline",
                        {
                            "spent_usd": round(budget.spent, 6),
                            "reserved_usd": round(budget.reserved, 6),
                            "cap_usd": budget.cap,
                            "case_ceiling_usd": budget.ceiling,
                        },
                    )
                finally:
                    log.close()
                return
            record.started = True
        try:
            if case.fault == "duplicate":
                barrier = threading.Barrier(2)

                def twice() -> None:
                    barrier.wait()
                    _submit(out, registry_dir, case, record)

                threads = [threading.Thread(target=twice) for _ in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            else:
                _submit(out, registry_dir, case, record)
        finally:
            try:
                budget.settle(n, _logged_cost(out, case.case_id))
            except Exception as exc:  # the drill still finishes; the check flags the case
                budget.release(n)
                record.errors.append(f"unexpected {type(exc).__name__} settling its cost")
            with lock:
                record.before_stop = progress["stopped_at"] is None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, cases))

    report = _report(out, registry_dir, records, budget, progress["stopped_at"], seed, workers)
    report["masked_by_stop"] = masked_by_stop(out, report)
    report["problems"] = check(out, report)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _report(out, registry_dir, records, budget, stop_id, seed, workers) -> dict:
    registry = Registry.from_dir(registry_dir)
    cases = []
    with _workflow(out, registry, None, crash=False) as workflow:
        for record in records.values():
            status = workflow.status(record.case.case_id) if record.started else None
            outcome = status["state"] if status else NOT_STARTED
            decided_by = (status.get("final") or {}).get("decided_by") if status else None
            cases.append(
                asdict(record.case)
                | {
                    "key_route": record.key_route,
                    "started": record.started,
                    "after_stop": record.after_stop,
                    "before_stop": record.before_stop and not record.after_stop,
                    "outcome": outcome,
                    "decided_by": decided_by,
                    "errors": sorted(set(record.errors)),
                }
            )
        verification = workflow.audit.verify()
    log = AuditLog(out / "audit.sqlite", read_only=True)
    try:
        spent = sum(
            e["payload"].get("cost_usd") or 0.0
            for e in log.entries()
            if e["event"] == "triage_result"
        )
    finally:
        log.close()
    table = Counter((c["fault"], c["outcome"]) for c in cases)
    in_flight = [c for c in cases if c["started"] and not c["after_stop"] and not c["before_stop"]]
    return {
        "out": str(out),
        "registry": str(registry_dir),
        "seed": seed,
        "workers": workers,
        "cases_total": len(cases),
        "by_fault_and_outcome": {f"{f} -> {o}": n for (f, o), n in sorted(table.items())},
        "stop": {
            "agent": STOPPED_AGENT,
            "stop_id": stop_id,
            "cases_after_stop": sum(c["after_stop"] for c in cases),
            # Begun before the stop, finished after it: a call in flight finishes (HM-19 gap).
            "in_flight_at_stop": len(in_flight),
            "in_flight_decided_automatically": sum(c["outcome"] == "decided" for c in in_flight),
        },
        "budget": {
            "cap_usd": budget.cap,
            "case_ceiling_usd": budget.ceiling,
            "spent_usd": round(spent, 6),
            "not_started": sum(c["outcome"] == NOT_STARTED for c in cases),
            "not_started_queued_for_a_person": 0,  # they never enter the workflow
            "reserved_left_usd": round(budget.reserved, 6),
        },
        "audit": {"ok": verification.ok, "entries": verification.entries},
        "cases": sorted(cases, key=lambda c: c["case_id"]),
    }


def _log_by_case(out: Path) -> dict[str, list[dict]]:
    log = AuditLog(out / "audit.sqlite", read_only=True)
    try:
        entries = log.entries()
    finally:
        log.close()
    by_case: dict[str, list[dict]] = {}
    for e in entries:
        by_case.setdefault(e["claim_id"], []).append(e)
    return by_case


def _last_result(events: list[dict]) -> dict | None:
    results = [e["payload"] for e in events if e["event"] == "triage_result"]
    return results[-1] if results else None


def masked_by_stop(out: Path, report: dict) -> list[str]:
    """Model-fault cases the owner's stop reached first (on the risk agent or later, or a wrong
    answer stopped before routing): the fault wasn't exercised. Listed, not counted as
    exercised and not a problem."""
    by_case = _log_by_case(out)
    masked = []
    for case in report["cases"]:
        if case["fault"] not in MODEL_FAULTS or not case["started"]:
            continue
        result = _last_result(by_case.get(case["case_id"], []))
        if result and STOPPED_REASON in (result["stopped_reason"] or ""):
            masked.append(case["case_id"])
    return sorted(masked)


def check(out: Path, report: dict) -> list[str]:
    """What the drill requires, read from the audit log and the workflow's status. IDs only."""
    problems = []
    by_case = _log_by_case(out)
    masked = set(report.get("masked_by_stop", ()))

    for case in report["cases"]:
        cid, fault, outcome = case["case_id"], case["fault"], case["outcome"]
        events = by_case.get(cid, [])
        names = [e["event"] for e in events]
        result = _last_result(events)
        failed = fault in MODEL_FAULTS or fault == "crash" or case["after_stop"]

        if any(e.startswith("unexpected ") for e in case["errors"]):
            problems.append(f"{cid}: unexpected error recorded ({'; '.join(case['errors'])})")
        if outcome not in WITH_A_PERSON | {NOT_STARTED, "decided"}:
            problems.append(f"{cid}: left {outcome}, with no one")
        if failed and outcome == "decided":
            problems.append(f"{cid}: {fault} case decided automatically")
        if fault == "none" and case["before_stop"] and not case["after_stop"]:
            expected = "decided" if case["key_route"] == "auto" else "waiting_for_adjuster"
            if outcome != expected:
                problems.append(f"{cid}: clean case ended {outcome}, expected {expected}")
        if names.count("evidence_pack") > 1:
            problems.append(f"{cid}: more than one evidence pack")
        if names.count("final_decision") > 1:
            problems.append(f"{cid}: decided twice")
        runs = names.count("triage_result")
        if runs > (2 if fault == "duplicate" else 1):
            problems.append(f"{cid}: triage ran {runs} times")

        # Every injected failure must show in the log, as itself.
        if not case["started"]:
            if names != ["batch_budget_reached"]:
                problems.append(f"{cid}: not started, but the log doesn't say why")
            continue
        if fault == "crash" and outcome != "needs_a_person":
            problems.append(f"{cid}: crash not reported as needing a person")
        if (
            fault in FAULT_REASONS
            and cid not in masked
            and not (
                result
                and result["route"] == "adjuster"
                and FAULT_REASONS[fault] in (result["stopped_reason"] or "")
            )
        ):
            problems.append(f"{cid}: {fault} not shown in the log by its own stop reason")
        if (
            fault == "wrong_answer"
            and cid not in masked
            and not (
                result
                and result["route"] == "adjuster"
                and any(OVER_CLAIM_REASON in r for r in result["routing_reasons"])
            )
        ):
            problems.append(f"{cid}: wrong answer not routed by the over-claim rule")
        if (
            case["after_stop"]
            and fault == "none"
            and not (result and STOPPED_REASON in (result["stopped_reason"] or ""))
        ):
            problems.append(f"{cid}: the stop isn't shown in the log")
        n = 2 if fault == "duplicate" else 1
        cost = sum(
            e["payload"].get("cost_usd") or 0.0 for e in events if e["event"] == "triage_result"
        )
        if cost > n * report["budget"]["case_ceiling_usd"]:
            problems.append(f"{cid}: cost {cost:.4f} above the case ceiling")

    if not report["audit"]["ok"]:
        problems.append("the audit log doesn't verify")
    budget = report["budget"]
    if budget["cap_usd"] is not None and budget["spent_usd"] > budget["cap_usd"]:
        problems.append(f"spent {budget['spent_usd']:.4f} over the cap {budget['cap_usd']:.4f}")
    if budget.get("reserved_left_usd", 0) > 1e-9:
        problems.append(f"{budget['reserved_left_usd']:.4f} of the cap still reserved at the end")
    return problems


def render(report: dict) -> str:
    lines = [
        f"Failure drill: {report['cases_total']} cases, {report['workers']} at a time, "
        f"seed {report['seed']} (scripted model; says nothing about the agents' accuracy)",
        "",
        "Fault -> outcome: cases",
    ]
    lines += [f"  {k}: {n}" for k, n in report["by_fault_and_outcome"].items()]
    stop, budget = report["stop"], report["budget"]
    cap = "none" if budget["cap_usd"] is None else f"${budget['cap_usd']:.2f}"
    lines += [
        "",
        f"Stop: {stop['agent']} ({stop['stop_id']}); "
        f"{stop['cases_after_stop']} cases begun after it; {stop['in_flight_at_stop']} in "
        f"flight at the stop, {stop['in_flight_decided_automatically']} of them then decided "
        "automatically (a call in flight finishes: HM-19 gap)",
        f"Cost: ${budget['spent_usd']:.4f} logged, cap {cap}; {budget['not_started']} not "
        "started (logged; never entered the workflow, so not queued for anyone)",
        f"Faults the stop reached first (not exercised): {len(report['masked_by_stop'])}",
        f"Audit log: {'verifies' if report['audit']['ok'] else 'DOES NOT VERIFY'}, "
        f"{report['audit']['entries']} entries",
        "",
    ]
    problems = report["problems"]
    lines.append("Problems: none" if not problems else "Problems:")
    lines += [f"  - {p}" for p in problems]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.drills", description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--fault-share", type=float, default=0.5)
    parser.add_argument("--stop-after", type=int, default=None)
    parser.add_argument("--cap-usd", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        report = run(
            args.out,
            rounds=args.rounds,
            workers=args.workers,
            seed=args.seed,
            fault_share=args.fault_share,
            stop_after=args.stop_after,
            cap_usd=args.cap_usd,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render(report))
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
