"""A fake Claude client that answers each agent with the ground truth for one claim."""

from types import SimpleNamespace

from hall_monitor.models import (
    CoverageFinding,
    CriticReview,
    GroundTruth,
    IntakeExtraction,
    Recommendation,
    RiskAssessment,
)


def outputs_from_truth(truth: GroundTruth) -> dict[type, object]:
    return {
        IntakeExtraction: truth.extraction,
        CoverageFinding: CoverageFinding(**truth.coverage.model_dump(exclude={"decision"})),
        RiskAssessment: RiskAssessment(**truth.risk.model_dump(), reasoning="from ground truth"),
        CriticReview: CriticReview(issues=[]),
        Recommendation: Recommendation(next_step=truth.next_step, reasons=["from ground truth"]),
    }


class FakeMessages:
    """Answers by output type. A list answers successive calls in order, repeating the last."""

    def __init__(self, outputs: dict[type, object], stop_reason: str = "end_turn"):
        self.outputs = outputs
        self.stop_reason = stop_reason
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.outputs.get(kwargs["output_format"])
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            parsed_output=answer,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )

    def formats(self) -> list[str]:
        return [c["output_format"].__name__ for c in self.calls]


def fake_client(outputs: dict[type, object] | None = None, stop_reason: str = "end_turn"):
    return SimpleNamespace(messages=FakeMessages(outputs or {}, stop_reason))
