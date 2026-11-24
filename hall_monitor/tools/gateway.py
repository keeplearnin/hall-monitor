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
import re
import time
from collections import Counter
from collections.abc import Callable, Coroutine, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from fastmcp import Client, FastMCP

from hall_monitor.gateway.injection import detect_in_texts
from hall_monitor.gateway.redaction import redact_value
from hall_monitor.no_send import tool_problems
from hall_monitor.registry import (
    ALL_DATA_CLASSES,
    RESERVED_AGENT_ID,
    DataClass,
    Registry,
    RegistryError,
    SendCapabilityError,
)
from hall_monitor.shared_models import ToolCall

if TYPE_CHECKING:
    from hall_monitor.delegation import Grant, GrantBook

PIPELINE_ACTOR = RESERVED_AGENT_ID  # no agent may take this ID (Registry refuses it)
# The argument a case's scope is checked against: a tool that takes it is policy-keyed.
SCOPE_ARGUMENT = "policy_number"
# How a record shows a tool name the model wrote that no server or manifest declares.
UNKNOWN_TOOL = "<not a known tool>"

# What an argument shape returns for a value the record may not keep.
NOT_IN_SHAPE = object()
# The value a call's record may keep, in canonical form, or NOT_IN_SHAPE.
type ArgumentShape = Callable[[object], object]


def shaped(pattern: re.Pattern[str], normalise: Callable[[str], str] = str) -> ArgumentShape:
    """A string that, normalised, wholly matches pattern; the record keeps the normalised form
    (the one the gateway compares), as a plain str."""

    def shape(value: object) -> object:
        if not isinstance(value, str):
            return NOT_IN_SHAPE
        canonical = str(normalise(value))
        return canonical if pattern.fullmatch(canonical) else NOT_IN_SHAPE

    return shape


def argument_sha256(value: object) -> str:
    """The hash of a withheld argument value: held in memory on the call, keyed by the audit
    log, never in the call's model_dump or JSON."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _strings(value: Any) -> Iterator[str]:
    """Every string in a tool result, keys included, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


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
        server: FastMCP,
        *,
        tool_data_classes: dict[str, frozenset[DataClass]],
        pipeline_tools: frozenset[str] = frozenset(),
        argument_shapes: Mapping[str, ArgumentShape] | None = None,
    ):
        """Each example passes its own server, what each tool returns (tool_data_classes; a
        tool missing from it counts as returning every data class), the tools its pipeline's
        own code may call (none unless given) and, by argument name, the shape of a value a
        call's record may keep, in canonical form (argument_shapes; any other value, and every
        value of an argument with no shape, is only named, and keyed in the audit log). There
        is no default server, so no example runs on another's tools or data classes by leaving
        an argument out (claims_gateway, in hall_monitor.claims.gateway, passes the claims
        ones). The gateway
        refuses to start on a server exposing any tool that could send (HM-25)."""
        self.registry = registry
        if type(server) is not FastMCP:
            # fastmcp's Client would treat a URL or a command as a remote server and post tool
            # calls to it, and a proxy (a FastMCP subclass) forwards them (HM-25): only a plain
            # in-process server is taken.
            raise TypeError("the tool gateway takes only an in-process FastMCP server")
        self.server = server
        self.tool_data_classes = tool_data_classes
        self.pipeline_tools = pipeline_tools
        self.argument_shapes = dict(argument_shapes or {})
        self._definitions = _run(self._list_tools())
        # Tool names code or a reviewed manifest declares: the only ones a record may copy.
        self._known = (
            set(self._definitions)
            | set(pipeline_tools)
            | {t for m in registry for t in m.allowed_tools}
        )
        problems = tool_problems(sorted(self._definitions))
        if problems:
            raise SendCapabilityError("the tool server may never send: " + "; ".join(problems))

    def _split(self, arguments: dict) -> tuple[dict, dict[str, str]]:
        """The values the record may keep, in canonical form, and the hashes of the rest."""
        kept: dict = {}
        withheld: dict[str, str] = {}
        for name, value in arguments.items():
            shape = self.argument_shapes.get(name)
            try:
                canonical = NOT_IN_SHAPE if shape is None else shape(value)
            except Exception:
                canonical = NOT_IN_SHAPE
            if canonical is NOT_IN_SHAPE:
                withheld[name] = argument_sha256(value)
            else:
                kept[name] = canonical
        return kept, withheld

    @staticmethod
    def _record(withheld: dict[str, str], **fields: Any) -> ToolCall:
        """A call's record: a withheld value is named, and its hash kept in memory only."""
        call = ToolCall(arguments_withheld=list(withheld), **fields)
        call._withheld_sha256 = dict(withheld)
        return call

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
        """scope_policy: the claim's own policy number. A lookup for any other is refused, and
        an agent given none may not call a tool that takes a policy number.

        grants and grant: the case's grant book and the grant the actor holds, required for an
        agent in a crew (HM-29) and checked on every call.

        Arguments the tool's schema doesn't name are refused before the server is called, and
        only the named ones are recorded: the rest are written by the model and could carry
        anything. A named one's value is kept only in its declared shape, otherwise only named. A
        result whose text addresses the AI is held back from an agent and the call records which
        signals matched (HM-07)."""
        started = time.perf_counter()
        # The model wrote the name: one nobody declared may carry anything, so the record (and
        # the refusal, which reaches the agent and the error field) never copies it (Review 46).
        shown = tool if isinstance(tool, str) and tool in self._known else UNKNOWN_TOOL
        definition = self._definitions.get(tool)
        properties = (definition or {}).get("input_schema", {}).get("properties", {})
        named = {k: v for k, v in arguments.items() if k in properties}
        # The values are the model's too: only one in its declared shape is kept.
        recorded, withheld = self._split(named)
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
            if definition is None:
                raise RegistryError(f"the tool server has no tool {tool!r}")
            if len(named) != len(arguments):
                # Counted, never named: the names are the model's.
                extra = len(arguments) - len(named)
                raise RegistryError(f"{extra} argument(s) the tool {tool!r} doesn't take")
            if actor != PIPELINE_ACTOR and scope_policy is None and SCOPE_ARGUMENT in properties:
                raise RegistryError(f"{actor} was given no claim to look up policies for")
            asked = str(arguments.get(SCOPE_ARGUMENT, "")).strip().upper()
            if scope_policy is not None and asked != scope_policy.strip().upper():
                # Not quoted here, where it would reach the agent and the error field; the
                # record keeps it only if it is policy-number shaped, otherwise names it.
                raise RegistryError(f"{actor} may only look up this claim's policy")
        except RegistryError as exc:
            why = str(exc)
            if shown != tool:
                # Every refusal quotes the name as repr; only that form is swapped, so a short
                # name ("o") can't rewrite the rest of the message.
                why = why.replace(repr(tool), repr(shown))
            log.append(
                self._record(
                    withheld,
                    actor=actor,
                    tool=shown,
                    arguments=recorded,
                    allowed=False,
                    result_sha256=None,
                    error=why,
                    seconds=round(time.perf_counter() - started, 3),
                )
            )
            return {"error": f"Refused: {why}"}

        counts: Counter[str] = Counter()
        try:
            result = _run(self._call(tool, arguments))
            # Redaction, screening and hashing inside the record too: if any fails, the call
            # is still recorded as allowed and failed.
            if actor != PIPELINE_ACTOR:
                result = redact_value(result, counts)
            signals = detect_in_texts(list(_strings(result)))
            result_sha256 = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        except Exception as exc:
            # Allowed but failed: still part of the record of what the agent tried to read.
            log.append(
                self._record(
                    withheld,
                    actor=actor,
                    tool=shown,
                    arguments=recorded,
                    allowed=True,
                    result_sha256=None,
                    # The type only: a server's message can quote a record or the arguments.
                    error=type(exc).__name__,
                    seconds=round(time.perf_counter() - started, 3),
                )
            )
            raise
        held = bool(signals) and actor != PIPELINE_ACTOR
        log.append(
            self._record(
                withheld,
                actor=actor,
                tool=shown,
                arguments=recorded,
                allowed=True,
                result_sha256=result_sha256,
                error="result held back: it addresses the AI" if held else None,
                identifiers_redacted={k: v for k, v in sorted(counts.items()) if v},
                injection_signals=signals,
                seconds=round(time.perf_counter() - started, 3),
            )
        )
        if held:
            # The pipeline's own code reads its results as data and is never steered by them;
            # an agent would read them as text, so it gets this instead. Claims routing reads
            # every call's injection_signals (the pipeline's too) and sends the case to a
            # person; no other example's routing reads them yet (only claims gives agents tools).
            return {
                "error": "Refused: the result reads as instructions to the AI and was held "
                "back; a person will review this case"
            }
        return result
