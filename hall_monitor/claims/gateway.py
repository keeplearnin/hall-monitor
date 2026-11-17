"""The claims tool gateway: the shared ToolGateway over the claims records server.

ToolGateway (hall_monitor.tools.gateway) takes its server, what each tool returns and the tools
the pipeline's own code may call as arguments; these are the claims ones. lookup_policy is the
claims pipeline's own policy check, made through the same audited path as the agents' calls.
"""

from __future__ import annotations

from fastmcp import FastMCP

from hall_monitor.claims.models import PolicyRecord
from hall_monitor.claims.tool_server import POLICY_LOOKUP, TOOL_DATA_CLASSES, build_server
from hall_monitor.registry import Registry
from hall_monitor.shared_models import ToolCall
from hall_monitor.tools.gateway import PIPELINE_ACTOR, ToolGateway

# The claims pipeline's own code checks may use these tools; it is not an agent and has no
# manifest.
PIPELINE_TOOLS = frozenset({POLICY_LOOKUP})


def claims_gateway(registry: Registry, server: FastMCP | None = None) -> ToolGateway:
    """The gateway for claims triage. server defaults to the records server built from the
    synthetic data; the fairness eval and tests pass one built from other records."""
    return ToolGateway(
        registry,
        build_server() if server is None else server,
        tool_data_classes=TOOL_DATA_CLASSES,
        pipeline_tools=PIPELINE_TOOLS,
    )


def lookup_policy(
    gateway: ToolGateway, policy_number: str | None, log: list[ToolCall]
) -> PolicyRecord | None:
    """The pipeline's own policy check, through the same audited path as the agents."""
    if not policy_number:
        return None
    result = gateway.call(PIPELINE_ACTOR, POLICY_LOOKUP, {"policy_number": policy_number}, log)
    if not result.get("found"):
        return None
    return PolicyRecord.model_validate({k: v for k, v in result.items() if k != "found"})
