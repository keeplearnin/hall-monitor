"""The MCP server, the tool gateway and the agents' tool-use loop (no network)."""

from types import SimpleNamespace

import pytest

from hall_monitor.agents.base import ToolAccess, run_agent
from hall_monitor.models import CoverageFinding, ToolCall
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import PIPELINE_ACTOR, ToolGateway

REGISTRY = Registry.from_dir()
GATEWAY = ToolGateway(REGISTRY)


def test_agents_are_offered_only_their_allowed_tools():
    names = lambda agent: sorted(d["name"] for d in GATEWAY.definitions(agent))  # noqa: E731
    assert names("intake") == []
    assert names("recommendation") == []
    assert names("coverage_check") == ["policy_lookup"]
    assert names("critic") == ["policy_lookup"]
    assert names("risk_fraud") == ["claims_history", "policy_lookup"]


def test_allowed_call_is_answered_and_recorded():
    log: list[ToolCall] = []
    result = GATEWAY.call(
        "coverage_check", "policy_lookup", {"policy_number": "WTP-ESS-100231"}, log
    )
    assert result["found"] and result["insured_name"] == "Daniel Okafor"
    [call] = log
    assert call.allowed and call.actor == "coverage_check" and len(call.result_sha256) == 64


def test_call_outside_the_allow_list_is_refused_and_recorded():
    log: list[ToolCall] = []
    result = GATEWAY.call(
        "coverage_check", "claims_history", {"policy_number": "WTP-ESS-100231"}, log
    )
    assert "Refused" in result["error"]
    [call] = log
    assert not call.allowed and call.result_sha256 is None and "may not call" in call.error


def test_pipeline_may_only_look_up_policies():
    log: list[ToolCall] = []
    assert GATEWAY.lookup_policy("WTP-ESS-100231", log).insured_name == "Daniel Okafor"
    assert GATEWAY.lookup_policy("WTP-NOPE-000000", log) is None
    result = GATEWAY.call(
        PIPELINE_ACTOR, "claims_history", {"policy_number": "WTP-ESS-100231"}, log
    )
    assert "Refused" in result["error"]
    assert [c.allowed for c in log] == [True, True, False]


def test_claims_history_follows_the_insured_across_policies():
    log: list[ToolCall] = []
    history = GATEWAY.call("risk_fraud", "claims_history", {"policy_number": "WTP-ESS-100902"}, log)
    assert history["insured_name"] == "Tomasz Wisniewski"
    assert [c["claim_ref"] for c in history["claims"]] == ["HIST-2025-1108"]
    none = GATEWAY.call("risk_fraud", "claims_history", {"policy_number": "WTP-ESS-100231"}, log)
    assert none["claims"] == []


# --- Tool-use loop ---------------------------------------------------------------


class ScriptedMessages:
    """Asks for one tool, then answers."""

    def __init__(self, tool: str, final):
        self.tool, self.final, self.calls = tool, final, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        if len(self.calls) == 1:
            block = SimpleNamespace(
                type="tool_use",
                id="tu_1",
                name=self.tool,
                input={"policy_number": "WTP-ESS-100231"},
            )
            return SimpleNamespace(
                stop_reason="tool_use", content=[block], parsed_output=None, usage=usage
            )
        return SimpleNamespace(
            stop_reason="end_turn", content=[], parsed_output=self.final, usage=usage
        )


@pytest.mark.parametrize(("tool", "allowed"), [("policy_lookup", True), ("claims_history", False)])
def test_tool_loop_runs_calls_through_the_gateway(tool, allowed):
    final = CoverageFinding(applicable_clauses=["2.1"], covered_amount=10.0, reasoning="ok")
    messages = ScriptedMessages(tool, final)
    access = ToolAccess(GATEWAY)

    run = run_agent(
        "coverage_check",
        registry=REGISTRY,
        client=SimpleNamespace(messages=messages),
        system="s",
        content="c",
        output_format=CoverageFinding,
        prompt_version="v",
        claim_id="claim_001",
        tools=access,
    )

    assert run.output == final and run.call.api_calls == 2
    assert [d["name"] for d in messages.calls[0]["tools"]] == ["policy_lookup"]
    tool_result = messages.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1"
    [call] = access.log
    assert call.tool == tool and call.allowed is allowed
