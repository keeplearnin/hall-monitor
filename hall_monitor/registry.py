"""Agent registry: every agent needs a hall pass before it can run.

A hall pass is a typed manifest in ``registry/<agent_id>.yaml`` naming the agent's
owner, risk tier, model and the tools it may call. The registry refuses any agent
without one, any agent whose manifest isn't active, and any tool the manifest
doesn't list.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
SCHEMA_FILENAME = "agent_manifest.schema.json"
APPROVALS_FILENAME = "approvals.yaml"


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
    owner: str = Field(min_length=1, description="Accountable person or team.")
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

    def digest(self) -> str:
        """SHA-256 of the canonical manifest, so a run can prove which hall pass it used."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
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


def load_approvals(path: Path = DEFAULT_REGISTRY_DIR / APPROVALS_FILENAME) -> dict[str, dict]:
    """Approved manifest digests and prompt hashes, per agent. Changes go through review."""
    return yaml.safe_load(path.read_text())["agents"]


class Registry:
    def __init__(
        self,
        manifests: Iterable[AgentManifest],
        approved_digests: dict[str, str] | None = None,
    ):
        """approved_digests=None skips the approval check; from_dir always enforces it."""
        self._approved = approved_digests
        self._manifests: dict[str, AgentManifest] = {}
        for manifest in manifests:
            if manifest.id in self._manifests:
                raise ValueError(f"duplicate agent id in registry: {manifest.id}")
            self._manifests[manifest.id] = manifest

    @classmethod
    def from_dir(cls, path: Path = DEFAULT_REGISTRY_DIR) -> Registry:
        manifests = []
        for file in sorted(path.glob("*.yaml")):
            if file.name == APPROVALS_FILENAME:
                continue
            manifest = AgentManifest.model_validate(yaml.safe_load(file.read_text()))
            if manifest.id != file.stem:
                raise ValueError(f"{file.name}: id {manifest.id!r} must match the file name")
            manifests.append(manifest)
        approvals = load_approvals(path / APPROVALS_FILENAME)
        return cls(manifests, {a: v["manifest_digest"] for a, v in approvals.items()})

    def get(self, agent_id: str) -> AgentManifest:
        try:
            return self._manifests[agent_id]
        except KeyError:
            raise UnregisteredAgentError(f"agent {agent_id!r} has no hall pass") from None

    def require(self, agent_id: str) -> AgentManifest:
        """Return the manifest for an agent that is allowed to run, or refuse."""
        manifest = self.get(agent_id)
        if manifest.status is not AgentStatus.ACTIVE:
            raise AgentNotActiveError(f"agent {agent_id!r} is {manifest.status}, not active")
        if self._approved is not None and self._approved.get(agent_id) != manifest.digest():
            raise UnapprovedManifestError(
                f"agent {agent_id!r} manifest {manifest.digest()[:12]} is not the approved version"
            )
        return manifest

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
