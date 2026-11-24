"""The one supervisor loop for claims, the complaint desk and leave.

Each workflow passes an action table. This loop asks the supervisor for one action, then
applies the same checks, in this order:

1. The case budget: steps, and when the workflow prices its calls, an unpriced cost, then
   cost and wall-clock time.
2. The kill switch: every agent the case has already used, before the supervisor is asked
   and again before the step it chose runs.
3. The choice. A step whose inputs are missing, a repeat on unchanged inputs, or a partial
   revision is refused. Nothing runs. After three refusals the case stops for a person.

A workflow can refuse a row for its own reason first (the critic is off, the revision limit
is used up). Finish and hand-off are terminal rows: the three checks do not apply, and the
workflow's own function routes the case.

The supervisor stays a classifier. It is not given tools, the loop is not a LangGraph graph,
and nothing here plans a sequence of steps. The work of a row is an ordinary function the
workflow passes in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hall_monitor.shared_models import StepOutcome, StepRefusal

MAX_REFUSALS = 3


class LoopFinished(BaseException):
    """A step's own function ended the case. Leave uses this when the rules refuse to go on.

    It is not a failure: the runner does not record the step as failed.
    """

    def __init__(self, value: Any):
        super().__init__(type(value).__name__)
        self.value = value


@dataclass(frozen=True)
class Action:
    """One row of a workflow's action table.

    ``missing`` is a step whose inputs are not ready. ``repeat`` is the same step on
    unchanged inputs. ``partial`` is a revision that does not send back every stage the
    critic raised. ``earlier`` is a refusal this row owns and that must win over those
    three. A terminal row is finish or hand-off.
    """

    missing: Callable[[], bool] | None = None
    repeat: Callable[[], bool] | None = None
    partial: Callable[[], StepRefusal | None] | None = None
    earlier: Callable[[], StepRefusal | None] | None = None
    terminal: bool = False
    run: Callable[[], list[str]] | None = None


def refusal_for(action: Action) -> StepRefusal | None:
    """Why this row may not run, or None when it may."""
    if action.terminal:
        return None
    if action.earlier is not None and (found := action.earlier()) is not None:
        return found
    if action.missing is not None and action.missing():
        return StepRefusal.PRECONDITION_UNMET
    if action.repeat is not None and action.repeat():
        return StepRefusal.REPEAT_WITHOUT_CHANGE
    if action.partial is not None and (found := action.partial()) is not None:
        return found
    return None


@dataclass
class SupervisorLoop:
    """What one workflow's case looks like to the shared loop. The callables are ordinary
    functions; they close over that case."""

    steps_taken: Callable[[], int]
    max_steps: int
    cost_usd: Callable[[], float]
    max_cost_usd: float
    elapsed: Callable[[], float]
    max_seconds: float
    recheck: Callable[[], None]
    ask: Callable[[], Any]
    action: Callable[[Any], Action]
    record: Callable[[Any, StepOutcome, StepRefusal | None, list[str]], None]
    authorize: Callable[[Any], None]
    before_work: Callable[[Any], None]
    fail_guard: Callable[[Any, Exception], None]
    fail_run: Callable[[Any, Exception], None]
    finish: Callable[[Any], Any]
    fail_finish: Callable[[Any, Exception], None]
    stopped: type[Exception]
    priced: bool = False
    unknown_cost: Callable[[], str | None] = lambda: None
    prepare_turn: Callable[[], None] = lambda: None


def _budget(loop: SupervisorLoop) -> None:
    if loop.steps_taken() >= loop.max_steps:
        raise loop.stopped(f"budget exhausted: {loop.max_steps} supervisor steps")
    if loop.priced:
        unknown = loop.unknown_cost()
        if unknown is not None:
            raise loop.stopped(f"budget can't be checked: {unknown}")
    spent = loop.cost_usd()
    if spent >= loop.max_cost_usd:
        raise loop.stopped(f"budget exhausted: cost reached {loop.max_cost_usd} USD")
    if loop.elapsed() >= loop.max_seconds:
        raise loop.stopped(f"budget exhausted: {loop.max_seconds:g} seconds")


def run_supervisor_loop(loop: SupervisorLoop) -> Any:
    """Ask, check, run or refuse, until the workflow finishes the case or the loop stops it."""
    refusals = 0
    try:
        while True:
            loop.prepare_turn()
            _budget(loop)
            loop.recheck()
            decision = loop.ask()
            row = loop.action(decision)
            if row.terminal:
                try:
                    return loop.finish(decision)
                except LoopFinished:
                    raise
                except Exception as exc:
                    loop.fail_finish(decision, exc)
                    raise
            found = refusal_for(row)
            if found is not None:
                refusals += 1
                loop.record(decision, StepOutcome.REFUSED, found, [])
                if refusals >= MAX_REFUSALS:
                    raise loop.stopped(f"supervisor loop: {MAX_REFUSALS} steps refused")
                continue
            try:
                loop.authorize(decision)
                loop.recheck()
                loop.before_work(decision)
            except LoopFinished:
                raise
            except Exception as exc:
                loop.fail_guard(decision, exc)
                raise
            try:
                agents = [] if row.run is None else row.run()
            except LoopFinished:
                raise
            except Exception as exc:
                loop.fail_run(decision, exc)
                raise
            loop.record(decision, StepOutcome.RAN, None, list(agents or []))
    except LoopFinished as done:
        return done.value
