"""Prompt caching, cache-aware cost per claim, and economy model routing (no network)."""

from types import SimpleNamespace

import pytest
import yaml

from hall_monitor.agents import coverage_check
from hall_monitor.agents.base import run_agent
from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.gateway.cost import call_cost
from hall_monitor.models import AgentCall, CoverageFinding, CriticReview, ReviewedStage
from hall_monitor.pipeline import triage
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, AgentManifest, Registry
from hall_monitor.tools.gateway import ToolGateway
from tests.fakes import fake_client, outputs_from_truth

CLAIMS = {c.claim_id: c for c in iter_claims()}
REGISTRY = Registry.from_dir()
GATEWAY = ToolGateway(REGISTRY)
WORDING = load_policy_wording()


def _call(model="claude-sonnet-5-5", **tokens):
    fields = {"input_tokens": 0, "output_tokens": 0} | tokens
    return AgentCall(
        agent_id="coverage_check",
        agent_version="x",
        model=model,
        manifest_digest="x",
        prompt_version="x",
        system_prompt_sha256="x",
        input_sha256="x",
        request_ids=[],
        stop_reason="end_turn",
        seconds=1.0,
        **fields,
    )


@pytest.mark.parametrize(
    ("tokens", "usd"),
    [
        ({"input_tokens": 1_000_000}, 2.00),
        ({"cache_creation_input_tokens": 1_000_000}, 2.50),  # 1.25x to write
        ({"cache_read_input_tokens": 1_000_000}, 0.20),  # 0.1x to read
        ({"output_tokens": 1_000_000}, 10.00),
    ],
)
def test_cost_prices_cached_input_separately(tokens, usd):
    assert call_cost(_call(**tokens)) == pytest.approx(usd)


def test_system_prompt_is_cacheable_and_cache_use_is_recorded():
    usage = SimpleNamespace(
        input_tokens=200,
        output_tokens=50,
        cache_read_input_tokens=1800,
        cache_creation_input_tokens=0,
    )
    final = CoverageFinding(applicable_clauses=["2.1"], covered_amount=1.0, reasoning="ok")
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[], parsed_output=final, usage=usage)

    run = run_agent(
        "coverage_check",
        registry=REGISTRY,
        client=SimpleNamespace(messages=SimpleNamespace(parse=parse)),
        system="the policy wording",
        prompt_template=coverage_check.SYSTEM_PROMPT,
        content="c",
        output_format=CoverageFinding,
        prompt_version="v",
        claim_id="claim_001",
    )
    [block] = calls[0]["system"]
    assert block["cache_control"] == {"type": "ephemeral"} and block["text"] == "the policy wording"
    assert run.call.cache_read_input_tokens == 1800 and run.call.input_tokens == 200


def test_economy_model_and_limit_go_together():
    raw = yaml.safe_load((DEFAULT_REGISTRY_DIR / "coverage_check.yaml").read_text())
    del raw["economy_max_claim_usd"]
    with pytest.raises(ValueError, match="together"):
        AgentManifest.model_validate(raw)


# --- Routing -----------------------------------------------------------------------


def _triage(claim_id, outputs=None, **kwargs):
    client = fake_client(outputs or outputs_from_truth(load_ground_truth(claim_id)))
    result = triage(
        CLAIMS[claim_id],
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=client,
        **kwargs,
    )
    return result, client.messages


def _models(result):
    return {c.agent_id: (c.model, c.model_tier) for c in result.agent_calls}


def test_small_claim_uses_economy_models_when_routing_is_on():
    result, _ = _triage("claim_002", model_routing=True)  # 282.49 claimed
    models = _models(result)
    assert models["coverage_check"] == ("claude-haiku-5-5", "economy")
    assert models["recommendation"] == ("claude-haiku-5-5", "economy")
    assert models["critic"] == ("claude-sonnet-5-5", "standard")  # never economised


def test_large_claim_stays_on_the_main_model():
    result, _ = _triage("claim_003", model_routing=True)  # 6,480 claimed
    assert _models(result)["coverage_check"] == ("claude-sonnet-5-5", "standard")


def test_routing_is_off_by_default():
    result, _ = _triage("claim_002")
    assert all(c.model_tier == "standard" for c in result.agent_calls)


def test_critic_revision_of_economy_work_uses_the_main_model():
    truth = load_ground_truth("claim_029")  # 318.40 claimed, 200 payable
    outputs = outputs_from_truth(truth)
    right = outputs[CoverageFinding]
    outputs[CoverageFinding] = [right.model_copy(update={"covered_amount": 318.40}), right]
    outputs[CriticReview] = [
        CriticReview(
            issues=[
                {
                    "stage": ReviewedStage.COVERAGE_CHECK,
                    "problem": "Cap not applied.",
                    "evidence": "3.1",
                }
            ]
        ),
        CriticReview(issues=[]),
    ]
    result, _ = _triage("claim_029", outputs, model_routing=True)
    coverage_calls = [c for c in result.agent_calls if c.agent_id == "coverage_check"]
    assert [c.model_tier for c in coverage_calls] == ["economy", "standard"]


def test_cost_per_claim_is_recorded():
    result, _ = _triage("claim_001")
    assert result.cost_usd == pytest.approx(sum(call_cost(c) for c in result.agent_calls), abs=1e-6)
    assert result.cost_usd > 0
