"""Delegation: when an agent hands work to another, the pass only narrows (HM-29, ADR 0025).

A grant is a pass for one case. Code issues the first one, to the agent leading the case; each
handoff makes a child grant that can hold no tool or data class its parent doesn't, nor any its
holder's own manifest doesn't. The case's grant book is the authority:

- a grant counts only if this book issued it (a copy with any field changed is refused);
- every use checks every agent in the grant's chain against the registry, so stopping the lead
  stops the helpers working for it at once;
- a handoff must be listed in the delegating agent's manifest (``may_delegate_to``), can't hand
  work back to an agent already in the chain, and stops ``MAX_HANDOFFS`` below the pipeline;
- what a grant allots its children can't add up to more than its own allotment;
- asking for more than the parent holds is refused, never quietly trimmed, and logged;
- closing the book ends every grant in it.

Every grant and refusal goes to the audit log as ``delegation_granted`` or
``delegation_refused``: IDs, the chain, tool names, data classes and amounts only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Iterable
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hall_monitor.governance.audit import AuditLog
from hall_monitor.registry import RESERVED_AGENT_ID, DataClass, Registry, RegistryError

PIPELINE = RESERVED_AGENT_ID  # code, which issues the first grant
MAX_HANDOFFS = 2  # agent-to-agent handoffs below the first grant
SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SLACK = 1e-9  # float slack when adding up allotments


class DelegationError(RegistryError):
    """A grant refused: not issued here, closed, undeclared, a loop, too deep or too wide."""


class Grant(BaseModel):
    """A pass for one case, held by one agent. Its ID covers every field, including its
    parent's ID, so a changed copy is a different grant the book never issued."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str = Field(pattern=SCOPE.pattern)
    holder: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    chain: tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")], ...] = Field(
        min_length=2
    )
    tools: tuple[str, ...]
    data_classes: tuple[DataClass, ...]
    budget_usd: float = Field(ge=0, allow_inf_nan=False)
    parent: str | None

    @property
    def grant_id(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    @property
    def handoffs(self) -> int:
        """Agent-to-agent handoffs that led to this grant (0 for the first grant)."""
        return len(self.chain) - 2


def _sorted_tools(tools: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(tools)))


def _sorted_classes(classes: Iterable[DataClass | str]) -> tuple[DataClass, ...]:
    return tuple(sorted({DataClass(c) for c in classes}, key=lambda c: c.value))


def _safe(name: str) -> str:
    """An agent or tool name for the log; a model may have written it, so anything that isn't
    a plain identifier is never copied."""
    return name if isinstance(name, str) and _NAME.match(name) else "<not an identifier>"


def _names(items: Iterable[DataClass | str]) -> str:
    return ", ".join(sorted(str(getattr(i, "value", i)) for i in items))


class GrantBook:
    """The grants for one case, for one run."""

    def __init__(self, registry: Registry, scope: str, *, audit: AuditLog | None = None):
        if not SCOPE.match(scope):
            raise ValueError("a grant scope must be a plain case ID")
        self.registry = registry
        self.scope = scope
        self._audit = audit
        self._issued: dict[str, Grant] = {}
        self._allotted: dict[str, float] = {}
        self._root: str | None = None
        self._closed = False
        # Issuing and handing off read and then write the allotment totals; one at a time.
        self._lock = threading.Lock()

    # Issuing.

    def issue(self, agent_id: str, *, budget_usd: float) -> Grant:
        """The first grant for the case, issued by code, holding the agent's manifest."""
        with self._lock:
            return self._issue(agent_id, budget_usd)

    def _issue(self, agent_id: str, budget_usd: float) -> Grant:
        self._open()
        if self._root is not None:
            raise DelegationError(f"{self.scope} already has its first grant")
        manifest = self.registry.require(agent_id)
        grant = Grant(
            scope=self.scope,
            holder=agent_id,
            chain=(PIPELINE, agent_id),
            tools=_sorted_tools(manifest.allowed_tools),
            data_classes=_sorted_classes(manifest.data_classes),
            budget_usd=budget_usd,
            parent=None,
        )
        self._root = grant.grant_id
        return self._record(grant, actor=PIPELINE)

    def delegate(
        self,
        parent: Grant,
        to: str,
        *,
        budget_usd: float,
        tools: Iterable[str] | None = None,
        data_classes: Iterable[DataClass | str] | None = None,
    ) -> Grant:
        """A child grant for ``to``, made by the parent's holder. With tools or data_classes
        left out, the child gets the overlap of the parent's grant and its own manifest; asking
        for anything outside that overlap is refused."""
        with self._lock:
            return self._delegate(parent, to, budget_usd, tools, data_classes)

    def _delegate(
        self,
        parent: Grant,
        to: str,
        budget_usd: float,
        tools: Iterable[str] | None,
        data_classes: Iterable[DataClass | str] | None,
    ) -> Grant:
        asked_tools = None if tools is None else _sorted_tools(tools)
        asked_classes = None if data_classes is None else _sorted_classes(data_classes)

        def refuse(code: str, message: str) -> DelegationError:
            self._log_refusal(parent, to, code, asked_tools, asked_classes, budget_usd)
            return DelegationError(message)

        self._open()
        if not self._is_issued(parent):
            raise refuse("not_issued", f"that grant was not issued for {self.scope}")
        try:
            self._require_chain(parent)
        except RegistryError:
            self._log_refusal(parent, to, "chain_refused", asked_tools, asked_classes, budget_usd)
            raise
        delegator = self.registry.require(parent.holder)
        if to not in delegator.may_delegate_to:
            raise refuse(
                "not_declared", f"agent {parent.holder!r} may not delegate to {_safe(to)!r}"
            )
        if to in parent.chain:
            raise refuse("loop", f"agent {_safe(to)!r} is already in the chain")
        if parent.handoffs + 1 > MAX_HANDOFFS:
            raise refuse("too_deep", f"a chain may be at most {MAX_HANDOFFS} handoffs: too deep")
        try:
            helper = self.registry.require(to)
        except RegistryError:
            self._log_refusal(parent, to, "helper_refused", asked_tools, asked_classes, budget_usd)
            raise

        may_tools = set(parent.tools) & set(helper.allowed_tools)
        may_classes = set(parent.data_classes) & set(helper.data_classes)
        child_tools = _sorted_tools(may_tools) if asked_tools is None else asked_tools
        child_classes = _sorted_classes(may_classes) if asked_classes is None else asked_classes
        wider_tools = set(child_tools) - may_tools
        wider_classes = set(child_classes) - may_classes
        if wider_tools or wider_classes:
            raise refuse(
                "wider_than_allowed",
                f"agent {_safe(to)!r} may not be given {_names(wider_tools | wider_classes)}: "
                f"outside {parent.holder!r}'s grant or {_safe(to)!r}'s manifest",
            )
        if not (isinstance(budget_usd, int | float) and math.isfinite(budget_usd)) or (
            budget_usd < 0
        ):
            raise refuse("bad_allotment", "an allotment must be a non-negative amount")
        given = self._allotted.get(parent.grant_id, 0.0)
        if given + budget_usd > parent.budget_usd + _SLACK:
            raise refuse(
                "over_allotment",
                f"{parent.holder!r} has {parent.budget_usd - given:.4f} USD of its allotment left",
            )

        grant = Grant(
            scope=self.scope,
            holder=to,
            chain=(*parent.chain, to),
            tools=child_tools,
            data_classes=child_classes,
            budget_usd=budget_usd,
            parent=parent.grant_id,
        )
        self._allotted[parent.grant_id] = given + budget_usd
        return self._record(grant, actor=parent.holder)

    # Using.

    def check(
        self,
        grant: Grant,
        *,
        tool: str | None = None,
        carries: frozenset[DataClass] | None = None,
    ) -> None:
        """Refuse unless this book issued the grant, it's still open, every agent in its chain
        may run, and the tool and data classes are inside it."""
        self._open()
        if not self._is_issued(grant):
            raise DelegationError(f"that grant was not issued for {self.scope}")
        self._require_chain(grant)
        if tool is not None and tool not in grant.tools:
            raise DelegationError(f"agent {grant.holder!r}'s grant doesn't hold tool {tool!r}")
        if carries is not None:
            extra = set(carries) - set(grant.data_classes)
            if extra:
                raise DelegationError(
                    f"agent {grant.holder!r}'s grant doesn't hold {_names(extra)} data"
                )

    def close(self) -> None:
        """End every grant in the book."""
        self._closed = True

    # Internals.

    def _open(self) -> None:
        if self._closed:
            raise DelegationError(f"the grant book for {self.scope} is closed")

    def _is_issued(self, grant: Grant) -> bool:
        return self._issued.get(grant.grant_id) == grant

    def _require_chain(self, grant: Grant) -> None:
        for agent_id in grant.chain[1:]:
            self.registry.require(agent_id)

    def _record(self, grant: Grant, *, actor: str) -> Grant:
        self._issued[grant.grant_id] = grant
        if self._audit is not None:
            self._audit.append(
                self.scope,
                "delegation_granted",
                actor,
                {
                    "grant": grant.grant_id,
                    "parent": grant.parent,
                    "holder": grant.holder,
                    "chain": list(grant.chain),
                    "tools": list(grant.tools),
                    "data_classes": [c.value for c in grant.data_classes],
                    "budget_usd": grant.budget_usd,
                },
            )
        return grant

    def _log_refusal(
        self,
        parent: Grant,
        to: str,
        code: str,
        tools: tuple[str, ...] | None,
        classes: tuple[DataClass, ...] | None,
        budget_usd: float,
    ) -> None:
        if self._audit is None:
            return
        budget = budget_usd if isinstance(budget_usd, int | float) else None
        if budget is not None and not math.isfinite(budget):
            budget = None
        self._audit.append(
            self.scope,
            "delegation_refused",
            # A grant that fails the "not issued" check may be a changed copy (model_copy skips
            # validation), so its holder is checked like any other name a model could write.
            _safe(parent.holder),
            {
                "parent": parent.grant_id,
                "to": _safe(to),
                "code": code,
                "tools": None if tools is None else [_safe(t) for t in tools],
                "data_classes": None if classes is None else [c.value for c in classes],
                "budget_usd": budget,
            },
        )
