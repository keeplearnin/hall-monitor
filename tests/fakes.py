"""A fake Claude client that answers each agent with the ground truth for one claim, and the
registry the tests run agents from."""

import atexit
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import yaml

from hall_monitor.agents.supervisor import scripted_answer
from hall_monitor.complaints.supervisor import DeskDecision, scripted_desk_answer
from hall_monitor.governance.approval import AdjusterDecision
from hall_monitor.governance.signing import demo_private_key
from hall_monitor.hr.supervisor import LeaveDecision, scripted_leave_answer
from hall_monitor.kill_switch import STOPS_FILENAME
from hall_monitor.models import (
    CoverageFinding,
    CriticReview,
    GroundTruth,
    IntakeExtraction,
    Recommendation,
    RiskAssessment,
    SupervisorDecision,
)
from hall_monitor.registry import APPROVALS_FILENAME, DEFAULT_REGISTRY_DIR


def outputs_from_truth(truth: GroundTruth) -> dict[type, object]:
    return {
        IntakeExtraction: truth.extraction,
        CoverageFinding: CoverageFinding(**truth.coverage.model_dump(exclude={"decision"})),
        RiskAssessment: RiskAssessment(**truth.risk.model_dump(), reasoning="from ground truth"),
        CriticReview: CriticReview(issues=[]),
        Recommendation: Recommendation(next_step=truth.next_step, reasons=["from ground truth"]),
    }


_DEFAULTS = {
    SupervisorDecision: scripted_answer,
    DeskDecision: scripted_desk_answer,
    LeaveDecision: scripted_leave_answer,
}


class FakeMessages:
    """Answers by output type. A list answers successive calls in order, repeating the last; a
    function is called with the request and returns the answer. stop_reason is one for every
    call, or a dict by output type (end_turn for the rest)."""

    def __init__(self, outputs: dict[type, object], stop_reason: str | dict = "end_turn"):
        self.outputs = outputs
        self.stop_reason = stop_reason
        self.calls: list[dict] = []  # the specialist agents' requests
        self.supervisor_calls: list[dict] = []  # the claims supervisor's, kept apart

    def parse(self, **kwargs):
        if kwargs["output_format"] in (SupervisorDecision, DeskDecision, LeaveDecision):
            self.supervisor_calls.append(kwargs)
        else:
            self.calls.append(kwargs)
        # The supervisor follows the old fixed order unless a test scripts it (ADR 0028).
        answer = self.outputs.get(kwargs["output_format"], _DEFAULTS.get(kwargs["output_format"]))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            answer = answer(kwargs)
        stop_reason = self.stop_reason
        if isinstance(stop_reason, dict):
            stop_reason = stop_reason.get(kwargs["output_format"], "end_turn")
        return SimpleNamespace(
            stop_reason=stop_reason,
            parsed_output=answer,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )

    def formats(self) -> list[str]:
        return [c["output_format"].__name__ for c in self.calls]


def specialists(calls: list) -> list:
    """Agent calls without the supervisors' turns."""
    supervisors = ("claims_supervisor", "complaint_supervisor", "leave_supervisor")
    return [c for c in calls if c.agent_id not in supervisors]


def fake_client(outputs: dict[type, object] | None = None, stop_reason: str | dict = "end_turn"):
    return SimpleNamespace(messages=FakeMessages(outputs or {}, stop_reason))


def signed_decision(claim_id: str, **fields) -> AdjusterDecision:
    """An adjuster decision signed with that adjuster's demo key, as the review CLI does."""
    decision = AdjusterDecision(**fields)
    return decision.signed(claim_id, demo_private_key(decision.adjuster_id))


def _registry_without_stops() -> Path:
    """The shipped registry with an empty stop list. Stops are live state: an owner may stop an
    agent at any time, and the tests must keep passing while one is stopped and after it is
    restarted. The kill switch's own tests start from this copy and add their stops to it."""
    root = Path(tempfile.mkdtemp(prefix="hall-monitor-registry-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    path = root / "registry"
    shutil.copytree(DEFAULT_REGISTRY_DIR, path)
    (path / STOPS_FILENAME).write_text(yaml.safe_dump({"stops": []}))
    approvals = yaml.safe_load((path / APPROVALS_FILENAME).read_text())
    for entry in approvals["agents"].values():
        entry.pop("cleared_stops", None)
    (path / APPROVALS_FILENAME).write_text(yaml.safe_dump(approvals, sort_keys=False))
    return path


TEST_REGISTRY_DIR = _registry_without_stops()
