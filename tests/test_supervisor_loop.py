"""The one supervisor loop: budget, kill switch, and the three refusals.

Claims, the complaint desk and leave used to copy this loop. These tests pin the shared
runner on its own, with a toy action table, before any workflow is wired to it.
"""

from enum import StrEnum

import pytest

from hall_monitor.agents.loop import (
    MAX_REFUSALS,
    Action,
    LoopFinished,
    SupervisorLoop,
    refusal_for,
    run_supervisor_loop,
)
from hall_monitor.shared_models import StepOutcome, StepRefusal


class Act(StrEnum):
    WORK = "work"
    REVISE = "revise"
    FINISH = "finish"


class Decision:
    def __init__(self, action: Act):
        self.action = action


class Stopped(Exception):
    pass


def _loop(**overrides):
    events: list[str] = []
    recorded: list[tuple] = []
    answers = iter(overrides.pop("answers", [Decision(Act.FINISH)]))

    def ask():
        events.append("ask")
        return next(answers)

    spec = SupervisorLoop(
        steps_taken=overrides.pop("steps_taken", lambda: 0),
        max_steps=overrides.pop("max_steps", 4),
        cost_usd=overrides.pop("cost_usd", lambda: 0.0),
        max_cost_usd=overrides.pop("max_cost_usd", 1.0),
        elapsed=overrides.pop("elapsed", lambda: 0.0),
        max_seconds=overrides.pop("max_seconds", 10.0),
        priced=overrides.pop("priced", False),
        unknown_cost=overrides.pop("unknown_cost", lambda: None),
        prepare_turn=overrides.pop("prepare_turn", lambda: events.append("prepare")),
        recheck=overrides.pop("recheck", lambda: events.append("recheck")),
        ask=ask,
        action=overrides.pop("action", lambda decision: Action(terminal=True)),
        record=lambda decision, outcome, refusal, agents: recorded.append(
            (decision.action, outcome, refusal, list(agents))
        ),
        authorize=overrides.pop("authorize", lambda decision: events.append("authorize")),
        before_work=overrides.pop("before_work", lambda decision: events.append("before_work")),
        fail_guard=overrides.pop(
            "fail_guard",
            lambda decision, exc: recorded.append((decision.action, StepOutcome.FAILED, None, [])),
        ),
        fail_run=overrides.pop(
            "fail_run",
            lambda decision, exc: recorded.append((decision.action, StepOutcome.FAILED, None, [])),
        ),
        finish=overrides.pop("finish", lambda decision: "done"),
        fail_finish=overrides.pop(
            "fail_finish",
            lambda decision, exc: recorded.append((decision.action, StepOutcome.FAILED, None, [])),
        ),
        stopped=Stopped,
    )
    return spec, events, recorded


def test_missing_inputs_are_refused_and_the_step_does_not_run():
    ran = []
    spec, events, recorded = _loop(
        answers=[Decision(Act.WORK), Decision(Act.FINISH)],
        action=lambda decision: (
            Action(missing=lambda: True, run=lambda: ran.append("ran") or [])
            if decision.action is Act.WORK
            else Action(terminal=True)
        ),
    )
    assert run_supervisor_loop(spec) == "done"
    assert ran == []
    assert recorded[0][1:] == (StepOutcome.REFUSED, StepRefusal.PRECONDITION_UNMET, [])
    assert "authorize" not in events


def test_a_repeat_on_unchanged_inputs_is_refused():
    spec, _, recorded = _loop(
        answers=[Decision(Act.WORK), Decision(Act.FINISH)],
        action=lambda decision: (
            Action(repeat=lambda: True, run=lambda: ["specialist"])
            if decision.action is Act.WORK
            else Action(terminal=True)
        ),
    )
    run_supervisor_loop(spec)
    assert recorded[0][1:] == (StepOutcome.REFUSED, StepRefusal.REPEAT_WITHOUT_CHANGE, [])


def test_a_partial_revision_is_refused():
    spec, _, recorded = _loop(
        answers=[Decision(Act.REVISE), Decision(Act.FINISH)],
        action=lambda decision: (
            Action(
                partial=lambda: StepRefusal.NOT_EVERY_RAISED_STAGE,
                run=lambda: ["intake"],
            )
            if decision.action is Act.REVISE
            else Action(terminal=True)
        ),
    )
    run_supervisor_loop(spec)
    assert recorded[0][2] is StepRefusal.NOT_EVERY_RAISED_STAGE


def test_a_workflow_refusal_beats_a_missing_input():
    """Critic-off is checked before 'inputs not ready', as the claims revise row does."""
    action = Action(
        earlier=lambda: StepRefusal.CRITIC_OFF,
        missing=lambda: True,
    )
    assert refusal_for(action) is StepRefusal.CRITIC_OFF


def test_three_refusals_stop_for_a_person():
    spec, _, recorded = _loop(
        answers=[Decision(Act.WORK)] * 5,
        action=lambda decision: Action(missing=lambda: True, run=lambda: ["specialist"]),
    )
    with pytest.raises(Stopped, match=f"supervisor loop: {MAX_REFUSALS} steps refused"):
        run_supervisor_loop(spec)
    assert len(recorded) == MAX_REFUSALS
    assert all(row[1] is StepOutcome.REFUSED for row in recorded)


def test_the_budget_is_checked_before_the_supervisor_is_asked():
    spec, events, recorded = _loop(steps_taken=lambda: 4, max_steps=4)
    with pytest.raises(Stopped, match="budget exhausted: 4 supervisor steps"):
        run_supervisor_loop(spec)
    assert events == ["prepare"]
    assert recorded == []


def test_an_unpriced_cost_stops_before_the_supervisor_is_asked():
    spec, events, _ = _loop(priced=True, unknown_cost=lambda: "cost unknown: demo")
    with pytest.raises(Stopped, match="budget can't be checked: cost unknown: demo"):
        run_supervisor_loop(spec)
    assert "ask" not in events


def test_the_kill_switch_is_checked_before_the_ask_and_again_before_the_step():
    calls = {"n": 0}

    def recheck():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("stopped")

    ran = []
    spec, events, recorded = _loop(
        recheck=recheck,
        answers=[Decision(Act.WORK)],
        action=lambda decision: Action(run=lambda: ran.append("ran") or ["specialist"]),
    )
    with pytest.raises(RuntimeError, match="stopped"):
        run_supervisor_loop(spec)
    assert ran == []
    # prepare, the first recheck, the ask, authorize, then the second recheck refuses the step.
    assert events == ["prepare", "ask", "authorize"]
    assert "before_work" not in events
    assert recorded == [(Act.WORK, StepOutcome.FAILED, None, [])]


def test_a_terminal_choice_does_not_run_a_specialist_or_count_as_a_refusal():
    ran = []
    spec, _, recorded = _loop(
        action=lambda decision: Action(terminal=True, run=lambda: ran.append("ran") or [])
    )
    assert run_supervisor_loop(spec) == "done"
    assert ran == []
    assert recorded == []


def test_a_step_that_ends_the_case_is_returned_without_a_failed_record():
    """Leave's rules can end the request from inside the step. That is not a failure."""

    def run():
        raise LoopFinished("to a person")

    spec, _, recorded = _loop(
        answers=[Decision(Act.WORK)],
        action=lambda decision: Action(run=run),
    )
    assert run_supervisor_loop(spec) == "to a person"
    assert recorded == []


def test_the_three_workflows_use_this_loop_and_do_not_keep_their_own():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    loop = (root / "hall_monitor/agents/loop.py").read_text()
    assert "MAX_REFUSALS = 3" in loop
    for relative in (
        "hall_monitor/claims/agentic.py",
        "hall_monitor/hr/pipeline.py",
        "hall_monitor/complaints/pipeline.py",
    ):
        source = (root / relative).read_text()
        assert "run_supervisor_loop" in source
        assert "MAX_REFUSALS" not in source
