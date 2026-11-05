"""Intake agent behaviour, with the Claude client faked out (no network)."""

from types import SimpleNamespace

import pytest

from hall_monitor.agents.base import AgentRefusedError, render_claim
from hall_monitor.agents.intake import run_intake
from hall_monitor.data import iter_claims, load_ground_truth
from hall_monitor.registry import Registry, UnregisteredAgentError

CLAIM = next(iter_claims())


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(stop_reason="end_turn", parsed_output=None):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=parsed_output,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )
    return SimpleNamespace(messages=FakeMessages(response))


def test_uses_the_model_on_the_hall_pass():
    expected = load_ground_truth(CLAIM.claim_id).extraction
    client = fake_client(parsed_output=expected)

    run = run_intake(CLAIM, Registry.from_dir(), client=client)
    assert run.output == expected
    assert run.call.manifest_digest == Registry.from_dir().get("intake").digest()
    assert client.messages.calls[0]["model"] == "claude-haiku-5-5"


def test_refuses_without_a_hall_pass():
    client = fake_client()
    with pytest.raises(UnregisteredAgentError):
        run_intake(CLAIM, Registry([]), client=client)
    assert client.messages.calls == []


def test_model_refusal_is_raised():
    with pytest.raises(AgentRefusedError):
        run_intake(CLAIM, Registry.from_dir(), client=fake_client(stop_reason="refusal"))


def test_claim_text_is_fenced_as_data():
    rendered = render_claim(CLAIM)
    assert rendered.startswith("<claim_submission>")
    assert rendered.endswith("</claim_submission>")
    assert all(f'filename="{a.filename}"' in rendered for a in CLAIM.attachments)


# --- From the whole-repo review (Phase B) -------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("currency", "Patient Ana Ruiz blood alcohol 0.12"),
        ("currency", "usd"),
        ("currency", "US Dollars"),
        ("policy_number", "Ana Ruiz, WTP-ESS-100231"),
        ("policy_number", "WTP ESS 100231"),
    ],
)
def test_codes_the_log_repeats_must_be_codes(field, value):
    """Currency and policy number reach routing reasons, corrections and rejections, so the
    model can't put free text there."""
    from pydantic import ValidationError

    from hall_monitor.data import load_ground_truth
    from hall_monitor.models import IntakeExtraction

    good = load_ground_truth("claim_001").extraction.model_dump()
    with pytest.raises(ValidationError):
        IntakeExtraction.model_validate(good | {field: value})
