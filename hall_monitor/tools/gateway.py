"""The tool gateway: every tool call passes the hall-pass allow-list check and is recorded.

Agents see only the tools their manifest allows, and every call is checked again here, so a
model that asks for anything else is refused even if it was never offered the tool. A tool is
also refused to an agent not approved for every data class it returns, and identifiers no agent
needs are removed from what it returns. Each call, allowed or refused, is appended to the
claim's audit log with a hash of what the agent received.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from fastmcp import Client, FastMCP

from hall_monitor.gateway.redaction import redact_value
from hall_monitor.models import PolicyRecord, ToolCall
from hall_monitor.no_send import tool_problems
from hall_monitor.registry import (
    ALL_DATA_CLASSES,
    RESERVED_AGENT_ID,
    DataClass,
    Registry,
    RegistryError,
    SendCapabilityError,
)
from hall_monitor.tools.server import POLICY_LOOKUP, TOOL_DATA_CLASSES, build_server

if TYPE_CHECKING:
    from hall_monitor.delegation import Grant, GrantBook

PIPELINE_ACTOR = RESERVED_AGENT_ID  # no agent may take this ID (Registry refuses it)
# The pipeline's own code checks may use these tools; it is not an agent and has no manifest.
PIPELINE_TOOLS = frozenset({POLICY_LOOKUP})


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion from sync code, even inside a running event loop (an async
    web app, an async LangGraph run): there it runs on a separate thread with its own loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class ToolGateway:
    def __init__(
        self,
        registry: Registry,
        server: FastMCP | None = None,
        *,
        tool_data_classes: dict[str, frozenset[DataClass]] | None = None,
        pipeline_tools: frozenset[str] | None = None,
    ):
        """server defaults to the claims records server; another example passes its own, with
        what each tool returns (tool_data_classes) and the tools its pipeline may call. The
        gateway refuses to start on a server exposing any tool that could send (HM-25)."""
        self.registry = registry
        if server is not None and type(server) is not FastMCP:
            # fastmcp's Client would treat a URL or a command as a remote server and post tool
            # calls to it, and a proxy (a FastMCP subclass) forwards them (HM-25): only a plain
            # in-process server is taken.
            raise TypeError("the tool gateway takes only an in-process FastMCP server")
        self.server = server or build_server()
        self.tool_data_classes = (
            TOOL_DATA_CLASSES if tool_data_classes is None else tool_data_classes
        )
        self.pipeline_tools = PIPELINE_TOOLS if pipeline_tools is None else pipeline_tools
        self._definitions = _run(self._list_tools())
        problems = tool_problems(sorted(self._definitions))
        if problems:
            raise SendCapabilityError("the tool server may never send: " + "; ".join(problems))

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

    def definitions(
        self, agent_id: str, *, grants: GrantBook | None = None, grant: Grant | None = None
    ) -> list[dict]:
        """Tool definitions for the Claude API: the agent's allow-list, less any tool returning
        data its manifest doesn't allow (it would only be refused). An agent in a crew is
        offered only what its grant holds, and nothing without one."""
        manifest = self.registry.require(agent_id)
        tools = list(manifest.allowed_tools)
        may_see = set(manifest.data_classes)
        if self.registry.in_crew(agent_id) or grants is not None or grant is not None:
            try:
                self._check_grant(agent_id, grants, grant)
            except RegistryError:
                return []
            tools = [t for t in tools if t in grant.tools]
            may_see &= set(grant.data_classes)
        return [
            self._definitions[name]
            for name in tools
            if name in self._definitions
            and self.tool_data_classes.get(name, ALL_DATA_CLASSES) <= may_see
        ]

    def _check_grant(
        self,
        actor: str,
        grants: GrantBook | None,
        grant: Grant | None,
        tool: str | None = None,
        carries: frozenset[DataClass] | None = None,
    ) -> None:
        """HM-29: an agent in a crew works only under a grant its case's book issued to it."""
        if grants is None or grant is None:
            raise RegistryError(f"agent {actor!r} is in a crew and needs a grant from its case")
        # The book first: a grant it didn't issue may carry any text, and this message reaches
        # the audit log, so the holder is never quoted.
        grants.check(grant, tool=tool, carries=carries)
        if grant.holder != actor:
            raise RegistryError(f"that grant is held by another agent, not {actor!r}")

    def call(
        self,
        actor: str,
        tool: str,
        arguments: dict,
        log: list[ToolCall],
        scope_policy: str | None = None,
        *,
        grants: GrantBook | None = None,
        grant: Grant | None = None,
    ) -> dict:
        """scope_policy: the claim's own policy number. A lookup for any other is refused.

        grants and grant: the case's grant book and the grant the actor holds, required for an
        agent in a crew (HM-29) and checked on every call."""
        started = time.perf_counter()
        try:
            if tool_problems([tool]):
                raise SendCapabilityError(f"tool {tool!r} may never be called: it could send")
            if actor == PIPELINE_ACTOR:
                if tool not in self.pipeline_tools:
                    raise RegistryError(f"pipeline may not call tool {tool!r}")
            else:
                carries = self.tool_data_classes.get(tool, ALL_DATA_CLASSES)
                self.registry.check_tool(actor, tool)
                self.registry.check_data(actor, carries)
                if self.registry.in_crew(actor) or grant is not None or grants is not None:
                    self._check_grant(actor, grants, grant, tool, carries)
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

        try:
            result = _run(self._call(tool, arguments))
        except Exception as exc:
            # Allowed but failed: still part of the record of what the agent tried to read.
            log.append(
                ToolCall(
                    actor=actor,
                    tool=tool,
                    arguments=arguments,
                    allowed=True,
                    result_sha256=None,
                    error=f"{type(exc).__name__}: {exc}",
                    seconds=round(time.perf_counter() - started, 3),
                )
            )
            raise
        counts: Counter[str] = Counter()
        if actor != PIPELINE_ACTOR:
            result = redact_value(result, counts)
        log.append(
            ToolCall(
                actor=actor,
                tool=tool,
                arguments=arguments,
                allowed=True,
                result_sha256=hashlib.sha256(
                    json.dumps(result, sort_keys=True).encode()
                ).hexdigest(),
                identifiers_redacted={k: v for k, v in sorted(counts.items()) if v},
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
