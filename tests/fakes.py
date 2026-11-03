"""A fake Claude client that answers each agent with the ground truth for one claim."""

from types import SimpleNamespace

from hall_monitor.models import (
    CoverageAssessment,
    GroundTruth,
    IntakeExtraction,
    Recommendation,
    RiskAssessment,
)


def outputs_from_truth(truth: GroundTruth) -> dict[type, object]:
    return {
        IntakeExtraction: truth.extraction,
        CoverageAssessment: CoverageAssessment(**truth.coverage.model_dump()),
        RiskAssessment: RiskAssessment(**truth.risk.model_dump(), reasoning="from ground truth"),
        Recommendation: Recommendation(next_step=truth.next_step, reasons=["from ground truth"]),
    }


class FakeMessages:
    def __init__(self, outputs: dict[type, object], stop_reason: str = "end_turn"):
        self.outputs = outputs
        self.stop_reason = stop_reason
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            parsed_output=self.outputs.get(kwargs["output_format"]),
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


def fake_client(outputs: dict[type, object] | None = None, stop_reason: str = "end_turn"):
    return SimpleNamespace(messages=FakeMessages(outputs or {}, stop_reason))
