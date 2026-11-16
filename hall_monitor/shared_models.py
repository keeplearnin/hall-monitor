"""Records every worked example shares: an agent call, a tool call through the gateway, and what
came of a supervisor's step. Each example's own types live beside its code (`hall_monitor.models`
for claims, `hall_monitor/<example>/models.py` for the others)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AgentCall(BaseModel):
    """One agent step: the hall pass it ran under and what it cost."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    agent_version: str
    model: str
    manifest_digest: str
    prompt_version: str
    # What exactly ran: the prompt template's hash (as approved in registry/approvals.yaml), hashes
    # of the rendered system prompt and input, and Anthropic's request ID.
    prompt_template_sha256: str | None = None
    system_prompt_sha256: str
    input_sha256: str
    request_ids: list[str]
    stop_reason: str | None
    api_calls: int = 1  # more than one when the agent used tools
    model_tier: str = "standard"  # "economy" when the routing rule chose the cheaper model
    input_tokens: int  # uncached input only
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int
    seconds: float


class ToolCall(BaseModel):
    """One tool call through the gateway, allowed or refused."""

    model_config = ConfigDict(extra="forbid")

    actor: str  # an agent ID, or "pipeline" for the code's own policy check
    tool: str
    arguments: dict
    allowed: bool
    result_sha256: str | None
    error: str | None = None
    # Identifiers removed from the result before the agent saw it, by type (never the values).
    identifiers_redacted: dict[str, int] = Field(default_factory=dict)
    seconds: float


class StepOutcome(StrEnum):
    RAN = "ran"
    FAILED = "failed"  # the step started and an agent or a code check stopped the case
    REFUSED = "refused"  # code refused the choice; nothing ran
    FINISHED = "finished"
    HANDED_OFF = "handed_off"


class StepRefusal(StrEnum):
    """Why code refused a supervisor's choice."""

    PRECONDITION_UNMET = "precondition_unmet"  # the step's inputs aren't there yet
    REPEAT_WITHOUT_CHANGE = "repeat_without_change"  # its inputs haven't changed since it ran
    CRITIC_OFF = "critic_off"
    NO_CRITIC_ISSUES = "no_critic_issues"  # revise without a current review that raised issues
    STAGE_WITHOUT_ISSUE = "stage_without_issue"  # revise a stage the critic didn't raise
    NOT_EVERY_RAISED_STAGE = "not_every_raised_stage"  # leave out a stage the critic raised
    REVISIONS_EXHAUSTED = "revisions_exhausted"
