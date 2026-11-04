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
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
SCHEMA_FILENAME = "agent_manifest.schema.json"
APPROVALS_FILENAME = "approvals.yaml"
ADJUSTERS_FILENAME = "adjusters.yaml"
# Files in registry/ that are not agent manifests.
NON_MANIFEST_FILES = frozenset({APPROVALS_FILENAME, ADJUSTERS_FILENAME})


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
    economy_model: str | None = Field(
        default=None,
        description="A cheaper model approved for small claims, used only when model routing "
        "is on. Revisions after a critic review always use the main model.",
    )
    economy_max_claim_usd: float | None = Field(
        default=None, gt=0, description="Largest total claimed that may use the economy model."
    )

    @model_validator(mode="after")
    def _economy_fields_together(self) -> AgentManifest:
        if (self.economy_model is None) != (self.economy_max_claim_usd is None):
            raise ValueError("economy_model and economy_max_claim_usd must be set together")
        return self

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
        approved_prompts: dict[str, str] | None = None,
    ):
        """approved_digests=None skips the approval check; from_dir always enforces it.

        approved_prompts maps agent IDs to the approved system-prompt template hash; when given,
        an agent run with any other prompt is refused.
        """
        self._approved = approved_digests
        self._approved_prompts = approved_prompts
        self._manifests: dict[str, AgentManifest] = {}
        for manifest in manifests:
            if manifest.id in self._manifests:
                raise ValueError(f"duplicate agent id in registry: {manifest.id}")
            self._manifests[manifest.id] = manifest

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
