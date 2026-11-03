"""The tool gateway: every tool call passes the hall-pass allow-list check and is recorded.

Agents see only the tools their manifest allows, and every call is checked again here, so a
model that asks for anything else is refused even if it was never offered the tool. Each call,
allowed or refused, is appended to the claim's audit log with a hash of what came back.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from fastmcp import Client, FastMCP

from hall_monitor.models import PolicyRecord, ToolCall
from hall_monitor.registry import Registry, RegistryError
from hall_monitor.tools.server import POLICY_LOOKUP, build_server

PIPELINE_ACTOR = "pipeline"
# The pipeline's own code checks may use these tools; it is not an agent and has no manifest.
PIPELINE_TOOLS = frozenset({POLICY_LOOKUP})


class ToolGateway:
    def __init__(self, registry: Registry, server: FastMCP | None = None):
        self.registry = registry
        self.server = server or build_server()
        self._definitions = asyncio.run(self._list_tools())

    async def _list_tools(self) -> dict[str, dict]:
        async with Client(self.server) as client:
            tools = await client.list_tools()
        return {
            t.name: {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        }

    async def _call(self, tool: str, arguments: dict) -> dict:
        async with Client(self.server) as client:
            result = await client.call_tool(tool, arguments)
        return result.structured_content or {}

    def definitions(self, agent_id: str) -> list[dict]:
        """Tool definitions for the Claude API, limited to the agent's allow-list."""
        allowed = self.registry.require(agent_id).allowed_tools
        return [self._definitions[name] for name in allowed if name in self._definitions]

    def call(
        self,
        actor: str,
        tool: str,
        arguments: dict,
        log: list[ToolCall],
        scope_policy: str | None = None,
    ) -> dict:
        """scope_policy: the claim's own policy number. A lookup for any other is refused."""
        started = time.perf_counter()
        try:
            if actor == PIPELINE_ACTOR:
                if tool not in PIPELINE_TOOLS:
                    raise RegistryError(f"pipeline may not call tool {tool!r}")
            else:
                self.registry.check_tool(actor, tool)
            asked = str(arguments.get("policy_number", "")).strip().upper()
            if scope_policy is not None and asked != scope_policy.strip().upper():
                raise RegistryError(
                    f"{actor} may only look up this claim's policy, not {asked or 'none'!r}"
                )
        except RegistryError as exc:
            log.append(
                ToolCall(
                    actor=actor,
                    tool=tool,
                    arguments=arguments,
                    allowed=False,
                    result_sha256=None,
                    error=str(exc),
                    seconds=round(time.perf_counter() - started, 3),
                )
            )
            return {"error": f"Refused: {exc}"}

        result = asyncio.run(self._call(tool, arguments))
        log.append(
            ToolCall(
                actor=actor,
                tool=tool,
                arguments=arguments,
                allowed=True,
                result_sha256=hashlib.sha256(
                    json.dumps(result, sort_keys=True).encode()
                ).hexdigest(),
                seconds=round(time.perf_counter() - started, 3),
            )
        )
        return result

    def lookup_policy(self, policy_number: str | None, log: list[ToolCall]) -> PolicyRecord | None:
        """The pipeline's own policy check, through the same audited path as the agents."""
        if not policy_number:
            return None
        result = self.call(PIPELINE_ACTOR, POLICY_LOOKUP, {"policy_number": policy_number}, log)
        if not result.get("found"):
            return None
        return PolicyRecord.model_validate({k: v for k, v in result.items() if k != "found"})
