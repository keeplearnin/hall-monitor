"""Agent registry: every agent needs a hall pass before it can run.

A hall pass is a typed manifest in ``registry/<agent_id>.yaml`` naming the agent's
owner, risk tier, model and the tools it may call. The registry refuses any agent
without one, any agent whose manifest isn't active, any agent its owner has stopped
(``registry/stops.yaml``, read on every call), and any tool the manifest doesn't list.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hall_monitor.kill_switch import STOPS_FILENAME, load_stops
from hall_monitor.no_send import tool_problems

RESERVED_AGENT_ID = "pipeline"  # the tool gateway's PIPELINE_ACTOR
DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
SCHEMA_FILENAME = "agent_manifest.schema.json"
APPROVALS_FILENAME = "approvals.yaml"
BUDGETS_FILENAME = "budgets.yaml"
ADJUSTERS_FILENAME = "adjusters.yaml"
OWNERS_FILENAME = "owners.yaml"
HR_STAFF_FILENAME = "hr_staff.yaml"
# Files in registry/ that are not agent manifests.
NON_MANIFEST_FILES = frozenset(
    {
        APPROVALS_FILENAME,
        ADJUSTERS_FILENAME,
        OWNERS_FILENAME,
        STOPS_FILENAME,
        HR_STAFF_FILENAME,
        BUDGETS_FILENAME,
    }
)


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentStatus(StrEnum):
    ACTIVE = "active"
    PLANNED = "planned"
    RETIRED = "retired"


class DataClass(StrEnum):
    """Categories of sensitive data an agent is approved to see."""

    PII = "pii"
    HEALTH = "health"
    FINANCIAL = "financial"


class AgentManifest(BaseModel):
    """The hall pass. Changing any field changes the digest recorded in the audit trail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="Stable agent identifier.")
    name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", description="Semantic version.")
    owner: str = Field(
        min_length=1, description="Accountable person: an ID from registry/owners.yaml."
    )
    purpose: str = Field(min_length=1)
    risk_tier: RiskTier
    status: AgentStatus
    model: str = Field(min_length=1, description="Model ID the agent is approved to use.")
    allowed_tools: tuple[str, ...] = Field(
        default=(), description="MCP tools the agent may call. Empty means none."
    )
    data_classes: tuple[DataClass, ...] = Field(
        default=(), description="Sensitive data categories the agent may receive."
    )
    economy_model: str | None = Field(
        default=None,
        description="A cheaper model approved for small claims, used only when model routing "
        "is on. Revisions after a critic review always use the main model.",
    )
    economy_max_claim_usd: float | None = Field(
        default=None, gt=0, description="Largest total claimed that may use the economy model."
    )
    may_delegate_to: tuple[str, ...] = Field(
        default=(),
        description="Agents this agent may hand work to, each under a narrower grant "
        "(hall_monitor/delegation.py). Empty means none.",
    )

    @model_validator(mode="after")
    def _economy_fields_together(self) -> AgentManifest:
        if (self.economy_model is None) != (self.economy_max_claim_usd is None):
            raise ValueError("economy_model and economy_max_claim_usd must be set together")
        return self

    @model_validator(mode="after")
    def _handoffs_name_other_agents(self) -> AgentManifest:
        if self.id in self.may_delegate_to:
            raise ValueError(f"agent {self.id!r} may not delegate to itself")
        if any(re.fullmatch(r"[a-z][a-z0-9_]*", a) is None for a in self.may_delegate_to):
            raise ValueError("may_delegate_to must list agent IDs")
        return self

    def digest(self) -> str:
        """SHA-256 of the canonical manifest, so a run can prove which hall pass it used. An
        empty may_delegate_to is left out, so manifests from before the field keep their
        approved digests."""
        fields = self.model_dump(mode="json")
        if not fields["may_delegate_to"]:
            del fields["may_delegate_to"]
        canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class RegistryError(PermissionError):
    """Base class for hall-pass refusals."""


class UnregisteredAgentError(RegistryError):
    pass


class AgentNotActiveError(RegistryError):
    pass


class ToolNotAllowedError(RegistryError):
    pass


class UnapprovedManifestError(RegistryError):
    pass


class DataClassNotAllowedError(RegistryError):
    pass


class AgentStoppedError(RegistryError):
    pass


class SendCapabilityError(RegistryError):
    """A tool that could send, or one not in the read-only catalogue (HM-25)."""


def check_no_send(manifest: AgentManifest) -> None:
    """Refuse a manifest that lists any tool outside the read-only catalogue (no_send.py)."""
    problems = tool_problems(manifest.allowed_tools)
    if problems:
        raise SendCapabilityError(f"agent {manifest.id!r} may never send: " + "; ".join(problems))


class StopListError(RegistryError):
    """The stop list can't be trusted, so every agent is refused until it is fixed."""


# What an agent input carries when the caller doesn't say: everything, so an undeclared input
# can only reach an agent approved for every data class.
ALL_DATA_CLASSES = frozenset(DataClass)


def load_approvals(path: Path = DEFAULT_REGISTRY_DIR / APPROVALS_FILENAME) -> dict[str, dict]:
    """Approved manifest digests and prompt hashes, per agent. Changes go through review."""
    return yaml.safe_load(path.read_text())["agents"]


def load_owners(path: Path = DEFAULT_REGISTRY_DIR / OWNERS_FILENAME) -> list[dict]:
    """The people who may own an agent. An owner is a person, never a team."""
    return yaml.safe_load(path.read_text())["owners"]


class Registry:
    def __init__(
        self,
        manifests: Iterable[AgentManifest],
        approved_digests: dict[str, str] | None = None,
        approved_prompts: dict[str, str] | None = None,
        *,
        stops_path: Path | None = None,
        cleared_stops: dict[str, frozenset[str]] | None = None,
        owners: frozenset[str] | None = None,
    ):
        """approved_digests=None skips the approval check; from_dir always enforces it.

        approved_prompts maps agent IDs to the approved system-prompt template hash; when given,
        an agent run with any other prompt is refused.

        stops_path is the stop list, read again on every check so a stop needs no restart;
        None skips it (from_dir always sets it). cleared_stops maps agent IDs to the stops a
        reviewed approval has cleared. owners, when given, are the people every manifest's
        owner and every stop's author must be.
        """
        self._approved = approved_digests
        self._approved_prompts = approved_prompts
        self._stops_path = stops_path
        self._cleared = cleared_stops or {}
        self._owners = owners
        self._manifests: dict[str, AgentManifest] = {}
        for manifest in manifests:
            if manifest.id in self._manifests:
                raise ValueError(f"duplicate agent id in registry: {manifest.id}")
            if manifest.id == RESERVED_AGENT_ID:
                # The tool gateway treats this actor as the pipeline and skips agent checks.
                raise ValueError(f"agent id {manifest.id!r} is reserved for the pipeline")
            if owners is not None and manifest.owner not in owners:
                raise ValueError(
                    f"agent {manifest.id!r}: owner {manifest.owner!r} is not a person listed "
                    f"in {OWNERS_FILENAME}"
                )
            self._manifests[manifest.id] = manifest
        for manifest in self._manifests.values():
            unknown = [a for a in manifest.may_delegate_to if a not in self._manifests]
            if unknown:
                raise ValueError(
                    f"agent {manifest.id!r} may delegate to agents with no hall pass: "
                    + ", ".join(unknown)
                )
        self._crew = frozenset(
            {m.id for m in self._manifests.values() if m.may_delegate_to}
            | {a for m in self._manifests.values() for a in m.may_delegate_to}
        )

    @classmethod
    def from_dir(cls, path: Path = DEFAULT_REGISTRY_DIR) -> Registry:
        manifests = []
        for file in sorted(path.glob("*.yaml")):
            if file.name in NON_MANIFEST_FILES:
                continue
            manifest = AgentManifest.model_validate(yaml.safe_load(file.read_text()))
            if manifest.id != file.stem:
                raise ValueError(f"{file.name}: id {manifest.id!r} must match the file name")
            manifests.append(manifest)
        approvals = load_approvals(path / APPROVALS_FILENAME)
        return cls(
            manifests,
            {a: v["manifest_digest"] for a, v in approvals.items()},
            {a: v["prompt_sha256"] for a, v in approvals.items() if "prompt_sha256" in v},
            stops_path=path / STOPS_FILENAME,
            cleared_stops={a: frozenset(v.get("cleared_stops", ())) for a, v in approvals.items()},
            owners=frozenset(p["id"] for p in load_owners(path / OWNERS_FILENAME)),
        )

    def get(self, agent_id: str) -> AgentManifest:
        try:
            return self._manifests[agent_id]
        except KeyError:
            raise UnregisteredAgentError(f"agent {agent_id!r} has no hall pass") from None

    def require(self, agent_id: str, *, prompt_sha256: str | None = None) -> AgentManifest:
        """Return the manifest for an agent that is allowed to run, or refuse.

        With prompt_sha256 (the hash of the system-prompt template about to run), the prompt
        must be the approved one too.
        """
        manifest = self.get(agent_id)
        if manifest.status is not AgentStatus.ACTIVE:
            raise AgentNotActiveError(f"agent {agent_id!r} is {manifest.status}, not active")
        check_no_send(manifest)
        self._check_stops(agent_id)
        if self._approved is not None and self._approved.get(agent_id) != manifest.digest():
            raise UnapprovedManifestError(
                f"agent {agent_id!r} manifest {manifest.digest()[:12]} is not the approved version"
            )
        if (
            prompt_sha256 is not None
            and self._approved_prompts is not None
            and self._approved_prompts.get(agent_id) != prompt_sha256
        ):
            raise UnapprovedManifestError(
                f"agent {agent_id!r} prompt {prompt_sha256[:12]} is not the approved version"
            )
        return manifest

    def _check_stops(self, agent_id: str) -> None:
        """Refuse an agent with an uncleared stop. Read on every call; any doubt refuses all."""
        if self._stops_path is None:
            return
        try:
            stops = load_stops(self._stops_path).stops
        except (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError) as exc:
            raise StopListError(
                f"{STOPS_FILENAME} can't be read ({type(exc).__name__}); every agent is refused"
            ) from None
        for stop in stops:
            # IDs and file names only in these messages: they reach the audit log.
            if stop.agent not in self._manifests:
                raise StopListError(f"{stop.id} names an unknown agent; every agent is refused")
            if self._owners is not None and stop.stopped_by not in self._owners:
                raise StopListError(
                    f"{stop.id} is by someone not in {OWNERS_FILENAME}; every agent is refused"
                )
        made = {(s.agent, s.id) for s in stops}
        for agent, cleared in self._cleared.items():
            if any((agent, stop_id) not in made for stop_id in cleared):
                raise StopListError(
                    f"approvals clear a stop for {agent!r} that {STOPS_FILENAME} doesn't hold; "
                    "every agent is refused"
                )
        live = [
            s for s in stops if s.agent == agent_id and s.id not in self._cleared.get(agent_id, ())
        ]
        if live:
            # IDs only: the reason may be personal and this message reaches the audit log.
            raise AgentStoppedError(
                f"agent {agent_id!r} is stopped ({live[-1].id} by {live[-1].stopped_by})"
            )

    def check_data(self, agent_id: str, carries: frozenset[DataClass]) -> None:
        """Refuse to send an agent data of a class its manifest doesn't allow."""
        extra = carries - set(self.require(agent_id).data_classes)
        if extra:
            names = ", ".join(sorted(c.value for c in extra))
            raise DataClassNotAllowedError(f"agent {agent_id!r} may not see {names} data")

    def in_crew(self, agent_id: str) -> bool:
        """True for an agent that may delegate or be delegated to: the tool gateway serves it
        only under a grant (HM-29)."""
        return agent_id in self._crew

    def check_tool(self, agent_id: str, tool: str) -> None:
        manifest = self.require(agent_id)
        if tool not in manifest.allowed_tools:
            raise ToolNotAllowedError(f"agent {agent_id!r} may not call tool {tool!r}")

    def __iter__(self) -> Iterator[AgentManifest]:
        return iter(self._manifests.values())

    def __len__(self) -> int:
        return len(self._manifests)


def manifest_json_schema() -> str:
    return json.dumps(AgentManifest.model_json_schema(), indent=2) + "\n"


if __name__ == "__main__":
    # Regenerate the published schema: uv run python -m hall_monitor.registry
    (DEFAULT_REGISTRY_DIR / SCHEMA_FILENAME).write_text(manifest_json_schema())
