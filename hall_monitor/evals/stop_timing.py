"""Kill-switch drill: how long a stop takes to halt an agentic case, measured offline.

    uv run python -m hall_monitor.evals.stop_timing             # prints the summary
    uv run python -m hall_monitor.evals.stop_timing --publish   # also writes RESULTS_DIR

For each workflow (claims, the complaint desk, HR leave) and each stopped agent (the workflow's
supervisor, and one specialist), it runs cases with a scripted model whose every call takes a
fixed simulated latency. A second thread writes the stop to the registry's stops file at a random
moment while the case is running, as an owner would with `python -m hall_monitor.kill_switch
stop`. It records:

- time_to_stop: from the stop being written to the case returning, routed to a person;
- calls_started_after_stop: model calls by the stopped agent that began after the stop was
  written (must be 0: every call checks the stops file first);
- other_agents_calls_after_stop: calls by the case's other agents after the stop. Every
  supervisor turn and every chosen step first re-checks every agent the case has used, so a
  stop on an agent that already answered ends the case at the next step; a stop on an agent
  not used yet takes effect when it is next needed, which can be one supervisor turn later;
- auto_after_stop: stopped cases that still went through automatically (claims) or to the light
  path (HR) (must be 0);
- stop_missed: cases where the stopped agent had already made its last call, so the case
  finished normally (reported, not a failure).

What it shows: a stop takes effect at the next model call or step, so time_to_stop is bounded by
the one call already in flight (here the simulated latency; live, one model call, seconds) plus
the loop's overhead. It does not measure a live model's latency, how long an owner takes to
decide to stop, or anything across machines beyond the shared stops file. No API calls.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from hall_monitor.agents.supervisor import scripted_answer
from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
from hall_monitor.complaints.pipeline import triage_complaint
from hall_monitor.complaints.supervisor import scripted_desk_answer
from hall_monitor.data import iter_claims, load_policy_wording
from hall_monitor.evals.agentic_compare import _client, _DeskScripted, _LeaveScripted
from hall_monitor.evals.meta import run_meta
from hall_monitor.hr.data import load_company, load_employees, load_messages
from hall_monitor.hr.pipeline import prepare as prepare_leave
from hall_monitor.hr.supervisor import scripted_leave_answer
from hall_monitor.kill_switch import STOPS_FILENAME, add_stop
from hall_monitor.models import Route
from hall_monitor.pipeline import triage
from hall_monitor.registry import APPROVALS_FILENAME, DEFAULT_REGISTRY_DIR, Registry
from hall_monitor.tools.gateway import ToolGateway

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "evals" / "results" / "kill-switch-drill"
MODE = "scripted-latency"
LATENCY_S = 0.05
OWNERS = {"claims": "own-301", "desk": "own-501", "leave": "own-401"}
TARGETS = {
    "claims": ("claims_supervisor", "coverage_check"),
    "desk": ("complaint_supervisor", "complaint_triage"),
    "leave": ("leave_supervisor", "leave_intake"),
}


# The answer type each stopped agent's calls ask for.
OUTPUT_OF = {
    "claims_supervisor": "SupervisorDecision",
    "coverage_check": "CoverageFinding",
    "complaint_supervisor": "DeskDecision",
    "complaint_triage": "ComplaintTriage",
    "leave_supervisor": "LeaveDecision",
    "leave_intake": "LeaveIntake",
}


class _Timed:
    """Wraps a scripted client's messages: every call sleeps the latency first and records when
    it started and for which answer type. A call the hall-pass check refuses never reaches
    here."""

    def __init__(self, inner, latency: float):
        self.inner, self.latency, self.starts = inner, latency, []

    def parse(self, **kwargs):
        self.starts.append((time.perf_counter(), kwargs["output_format"].__name__))
        time.sleep(self.latency)
        return self.inner.parse(**kwargs)


def _registry_copy(root: Path) -> Path:
    """The shipped registry with an empty stop list, as the failure drills use."""
    path = root / "registry"
    shutil.copytree(
        DEFAULT_REGISTRY_DIR, path, ignore=shutil.ignore_patterns(".*", "*.schema.json")
    )
    (path / STOPS_FILENAME).write_text("stops: []\n")
    approvals = yaml.safe_load((path / APPROVALS_FILENAME).read_text())
    for entry in approvals["agents"].values():
        entry.pop("cleared_stops", None)
    (path / APPROVALS_FILENAME).write_text(yaml.safe_dump(approvals, sort_keys=True))
    return path


def _case(workflow: str, registry: Registry, latency: float):
    """A function running one case (returning whether it went through without a person's
    review, and its stop reason), and its timed client."""
    if workflow == "claims":
        claim = next(c for c in iter_claims() if c.claim_id == "claim_001")  # auto when clean
        messages = _Timed(_client(claim.claim_id, scripted_answer).messages, latency)
        gateway, wording = ToolGateway(registry), load_policy_wording()

        def run():
            result = triage(
                claim,
                registry=registry,
                gateway=gateway,
                wording=wording,
                client=SimpleNamespace(messages=messages),
            )
            return result.routing.route is Route.AUTO, result.stopped_reason

        return run, messages
    if workflow == "desk":
        complaint = load_pinned(PINNED_NAME)[0]
        messages = _Timed(_DeskScripted(scripted_desk_answer, False), latency)

        def run():
            result = triage_complaint(
                complaint, registry=registry, client=SimpleNamespace(messages=messages)
            )
            return False, result.stopped_reason  # every complaint goes to a person

        return run, messages
    message = next(m for m in load_messages() if m.id == "lr_001")
    messages = _Timed(_LeaveScripted(scripted_leave_answer, False), latency)
    company, employees = load_company(), load_employees()

    def run():
        result = prepare_leave(
            message,
            employees=employees,
            company=company,
            registry=registry,
            client=SimpleNamespace(messages=messages),
        )
        return result.route.route == "specialist_sign_off", result.stopped_reason

    return run, messages


def drill(workflow: str, agent: str, trials: int, latency: float, seed: int) -> dict:
    rng = random.Random(seed)
    times: list[float] = []
    after = others = autos = missed = 0
    for trial in range(trials):
        with tempfile.TemporaryDirectory() as tmp:
            path = _registry_copy(Path(tmp))
            registry = Registry.from_dir(path)
            run_case, messages = _case(workflow, registry, latency)
            delay = rng.uniform(0.2, 3.5) * latency  # inside the case's first few calls
            written: list[float] = []

            def stop(delay=delay, path=path, written=written, trial=trial):
                time.sleep(delay)
                add_stop(
                    path / STOPS_FILENAME,
                    stop_id=f"stop-{trial + 1:04d}",
                    agent=agent,
                    by=OWNERS[workflow],
                    reason="kill-switch drill",
                )
                written.append(time.perf_counter())

            thread = threading.Thread(target=stop)
            thread.start()
            auto, stopped_reason = run_case()
            ended = time.perf_counter()
            thread.join()
            if stopped_reason is None:
                missed += 1
                continue
            t0 = written[0]
            times.append(ended - t0)
            after += sum(t > t0 and kind == OUTPUT_OF[agent] for t, kind in messages.starts)
            others += sum(t > t0 and kind != OUTPUT_OF[agent] for t, kind in messages.starts)
            autos += auto
    times.sort()
    return {
        "workflow": workflow,
        "stopped_agent": agent,
        "trials": trials,
        "stopped": len(times),
        "stop_missed": missed,
        "calls_started_after_stop": after,
        "other_agents_calls_after_stop": others,
        "auto_after_stop": autos,
        "time_to_stop_s": {
            "median": round(statistics.median(times), 4) if times else None,
            "p95": round(times[int(0.95 * (len(times) - 1))], 4) if times else None,
            "max": round(times[-1], 4) if times else None,
        },
    }


def run(trials: int = 20, latency: float = LATENCY_S, seed: int = 7) -> dict:
    rows = [
        drill(workflow, agent, trials, latency, seed + 10 * i + j)
        for i, (workflow, agents) in enumerate(TARGETS.items())
        for j, agent in enumerate(agents)
    ]
    agents = sorted({a for pair in TARGETS.values() for a in pair})
    maxima = [r["time_to_stop_s"]["max"] for r in rows if r["time_to_stop_s"]["max"] is not None]
    return {
        "meta": run_meta(Registry.from_dir(), agents, mode=MODE, latency_s=latency, trials=trials),
        "rows": rows,
        "summary": {
            "calls_started_after_stop": sum(r["calls_started_after_stop"] for r in rows),
            "max_other_agents_calls_after_stop": max(
                r["other_agents_calls_after_stop"] for r in rows
            ),
            "auto_after_stop": sum(r["auto_after_stop"] for r in rows),
            "stopped_cases": sum(r["stopped"] for r in rows),
            "max_time_to_stop_s": max(maxima, default=None),
            "latency_s": latency,
        },
    }


def report(result: dict) -> str:
    latency = result["summary"]["latency_s"]
    lines = [
        "# Kill-switch drill (offline, scripted model)",
        "",
        f"Run {result['meta']['run_at']} at {result['meta']['git_commit'][:12]}. Every model "
        f"call takes a simulated {latency * 1000:.0f} ms; a second thread writes the stop at a "
        "random moment in the case. No API calls: live, the call in flight is a real model "
        "call (seconds), so live time-to-stop is about one call, not these milliseconds.",
        "",
        "| Workflow | Agent stopped | Trials | Stopped | Stop landed after its last call | "
        "Its calls after stop | Other agents' calls after stop | "
        "Through without review after stop | Time to stop, median / p95 / max (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in result["rows"]:
        t = r["time_to_stop_s"]
        lines.append(
            f"| {r['workflow']} | `{r['stopped_agent']}` | {r['trials']} | {r['stopped']} | "
            f"{r['stop_missed']} | {r['calls_started_after_stop']} | "
            f"{r['other_agents_calls_after_stop']} | {r['auto_after_stop']} | "
            f"{t['median']} / {t['p95']} / {t['max']} |"
        )
    lines += [
        "",
        "- **Its calls after stop** and **through without review after stop** must be 0 (gated).",
        "- **Other agents' calls after stop:** at most the supervisor turn before the stopped "
        "agent is next needed (a stop on an agent the case already used ends it at the next "
        "step).",
        "- **Time to stop** is the call in flight when the stop was written plus the loop's "
        f"overhead: about one {latency * 1000:.0f} ms call here.",
        "- **Stop landed after its last call:** the stopped agent had already made its last "
        "call, so the case finished normally.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.evals.stop_timing")
    parser.add_argument("--publish", action="store_true", help=f"write {RESULTS_DIR}")
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args(argv)
    result = run(args.trials)
    text = report(result)
    print(text)
    if args.publish:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        (RESULTS_DIR / "report.md").write_text(text)
    summary = result["summary"]
    return 0 if summary["calls_started_after_stop"] == 0 and summary["auto_after_stop"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
