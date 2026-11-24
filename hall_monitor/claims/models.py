"""Typed shapes for insurance claims triage: claims, agent outputs, the triage record and ground
truth. The records every worked example shares are in `hall_monitor.shared_models`."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hall_monitor.shared_models import AgentCall, StepOutcome, StepRefusal, ToolCall


class ClaimType(StrEnum):
    TRIP_CANCELLATION = "trip_cancellation"
    BAGGAGE_DELAY = "baggage_delay"
    BAGGAGE_LOSS = "baggage_loss"
    EMERGENCY_MEDICAL = "emergency_medical"


class Plan(StrEnum):
    ESSENTIALS = "essentials"
    PLUS = "plus"


# --- Inputs ---------------------------------------------------------------


# The channel and an attachment's kind come with the claim, but they reach every prompt (and the
# channel the audit log's actor), so only known values load: a free-form code could still
# spell a name or an instruction.
Channel = Literal["email", "mobile_app", "web_form"]
AttachmentKind = Literal[
    "booking_confirmation",
    "carrier_letter",
    "certificate",
    "document",
    "invoice",
    "letter",
    "medical_report",
    "receipt",
]


# Wayfarer policy numbers: plan code and six digits, e.g. WTP-ESS-100231.
POLICY_NUMBER_PATTERN = re.compile(r"\bWTP-(?:ESS|PLS)-\d{6}\b")
# As a claimant or an OCR'd page may write one: any case, a space or a line break for a hyphen.
_POLICY_NUMBER_WRITTEN = re.compile(r"\bWTP[-\s]{1,3}(ESS|PLS)[-\s]{1,3}(\d{6})\b", re.IGNORECASE)


def policy_numbers_in_text(text: str) -> list[str]:
    """Policy numbers in a text, however written, in the record's form (WTP-ESS-100231), sorted."""
    return sorted(
        {f"WTP-{kind.upper()}-{digits}" for kind, digits in _POLICY_NUMBER_WRITTEN.findall(text)}
    )


def policy_numbers_in(claim: ClaimSubmission) -> list[str]:
    """Policy numbers in the claim as sent (narrative and documents): found by code, not by an
    agent."""
    return policy_numbers_in_text(
        "\n".join([claim.narrative, *(a.text for a in claim.attachments)])
    )


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    kind: AttachmentKind
    text: str = Field(description="Text content of the document (OCR output in production).")


class ClaimSubmission(BaseModel):
    """What the customer sent. Everything here is untrusted input."""

    model_config = ConfigDict(extra="forbid")

    # It reaches every audit entry, the recommender's input and traces, so it must be a code.
    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    submitted_at: datetime
    channel: Channel
    narrative: str
    attachments: list[Attachment]


class Trip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str
    departure_date: date
    return_date: date
    initial_deposit_date: date


class PolicyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_number: str
    plan: Plan
    insured_name: str
    purchase_date: date
    coverage_start: date
    coverage_end: date
    trip: Trip


# --- Agent outputs --------------------------------------------------------


class ClaimedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    # Non-finite amounts (NaN, infinity) are refused: every comparison with NaN is false, so it
    # would slip past the routing rule's checks.
    amount: float = Field(
        allow_inf_nan=False, description="Amount claimed for this item, in the claim currency."
    )


class IntakeExtraction(BaseModel):
    """Output of the intake agent. Fields not stated in the submission must be null."""

    model_config = ConfigDict(extra="forbid")

    claimant_name: str
    # Repeated in corrections, routing reasons and rejections, so it must be code-shaped: no
    # spaces, no free text from the model.
    policy_number: str | None = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$")
    claim_type: ClaimType
    incident_date: date | None = Field(
        description="Date the insured event happened (illness, injury, bag delay or loss), "
        "not the date the claim was filed."
    )
    incident_location: str | None = Field(
        description="Where the event happened, as the narrative names it; for baggage, where the "
        "delay or loss was discovered on arrival. Null if the narrative names no place."
    )
    claimed_items: list[ClaimedItem]
    total_claimed: float = Field(allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code, e.g. USD.")
    supporting_documents: list[str] = Field(description="Filenames of attachments provided.")


class CoverageDecision(StrEnum):
    COVERED = "covered"
    PARTIALLY_COVERED = "partially_covered"
    NOT_COVERED = "not_covered"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskFlag(StrEnum):
    POLICY_PURCHASED_SHORTLY_BEFORE_INCIDENT = "policy_purchased_shortly_before_incident"
    RECEIPT_DATED_AFTER_INCIDENT = "receipt_dated_after_incident"
    MISSING_CARRIER_REPORT = "missing_carrier_report"
    DUPLICATE_RECEIPT = "duplicate_receipt"
    AMOUNTS_INCONSISTENT = "amounts_inconsistent"
    DATES_INCONSISTENT = "dates_inconsistent"
    LATE_SUBMISSION = "late_submission"
    PRIOR_SIMILAR_CLAIM = "prior_similar_claim"


class NextStep(StrEnum):
    PAY = "pay"
    REQUEST_INFORMATION = "request_information"
    INVESTIGATE = "investigate"
    DENY = "deny"


class Route(StrEnum):
    AUTO = "auto"
    ADJUSTER = "adjuster"


class CoverageFinding(BaseModel):
    """What the coverage check agent returns. The decision label is added in code."""

    model_config = ConfigDict(extra="forbid")

    applicable_clauses: list[str] = Field(
        description='Clause numbers that decide the outcome or amount, e.g. "3.1", "5.1".'
    )
    covered_amount: float = Field(
        allow_inf_nan=False, description="What the policy pays in USD. 0 if not covered."
    )
    reasoning: str


class CoverageAssessment(BaseModel):
    """A coverage finding plus the decision label, which follows from the amount."""

    model_config = ConfigDict(extra="forbid")

    decision: CoverageDecision
    applicable_clauses: list[str]
    covered_amount: float = Field(allow_inf_nan=False)
    reasoning: str

    @classmethod
    def from_finding(cls, finding: CoverageFinding, total_claimed: float) -> CoverageAssessment:
        if finding.covered_amount <= 0:
            decision = CoverageDecision.NOT_COVERED
        elif finding.covered_amount >= total_claimed - 0.005:
            decision = CoverageDecision.COVERED
        else:
            decision = CoverageDecision.PARTIALLY_COVERED
        return cls(decision=decision, **finding.model_dump())


class RiskAssessment(BaseModel):
    """Output of the risk and fraud agent."""

    model_config = ConfigDict(extra="forbid")

    level: RiskLevel
    flags: list[RiskFlag]
    reasoning: str


class ReviewedStage(StrEnum):
    INTAKE = "intake"
    COVERAGE_CHECK = "coverage_check"
    RISK_FRAUD = "risk_fraud"


class CriticIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: ReviewedStage
    problem: str = Field(description="What is wrong, specifically.")
    evidence: str = Field(description="The document, clause or figure that shows it.")


class CriticReview(BaseModel):
    """Output of the critic agent. No issues means the work is approved."""

    model_config = ConfigDict(extra="forbid")

    issues: list[CriticIssue]


class Recommendation(BaseModel):
    """Output of the recommendation agent. A proposal; an adjuster decides consequential cases."""

    model_config = ConfigDict(extra="forbid")

    next_step: NextStep
    reasons: list[str] = Field(description="Each reason with the evidence behind it.")


# --- Pipeline record ------------------------------------------------------


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Route
    reasons: list[str]


class RevisionRecord(BaseModel):
    """One critic-driven revision: the stage's answer before and after, and what changed."""

    model_config = ConfigDict(extra="forbid")

    stage: ReviewedStage
    round: int
    before: dict
    after: dict
    changed_fields: list[str]


class SupervisorAction(StrEnum):
    """What the claims supervisor may choose next (ADR 0028). A closed set: anything else fails
    validation, and code checks each choice before it runs."""

    RUN_INTAKE = "run_intake"
    RUN_COVERAGE_CHECK = "run_coverage_check"
    RUN_RISK_FRAUD = "run_risk_fraud"
    RUN_CRITIC = "run_critic"
    REVISE = "revise"  # send the stages the critic raised back for correction
    RUN_RECOMMENDATION = "run_recommendation"
    FINISH = "finish"  # ask code to route the claim; code alone can choose auto
    HAND_OFF = "hand_off"  # send the claim to a person now


class SupervisorReason(StrEnum):
    """Why the supervisor chose its action: a code, so the audit log holds no free text."""

    NEEDS_EXTRACTION = "needs_extraction"
    NEEDS_POLICY = "needs_policy"
    NEEDS_COVERAGE = "needs_coverage"
    NEEDS_RISK = "needs_risk"
    NEEDS_REVIEW = "needs_review"
    RERUN_AFTER_CHANGE = "rerun_after_change"
    CRITIC_RAISED_ISSUES = "critic_raised_issues"
    READY_TO_RECOMMEND = "ready_to_recommend"
    WORK_COMPLETE = "work_complete"
    INJECTION_SUSPECTED = "injection_suspected"
    STEP_REFUSED = "step_refused"
    REVISIONS_EXHAUSTED = "revisions_exhausted"
    BUDGET_LOW = "budget_low"
    UNCERTAIN = "uncertain"


class SupervisorDecision(BaseModel):
    """What the supervisor agent returns: one next step. It has no route field: it can ask code
    to finish, never choose auto."""

    model_config = ConfigDict(extra="forbid")

    action: SupervisorAction
    reason: SupervisorReason
    stages: list[ReviewedStage] = Field(
        default_factory=list, description="For revise only: the stages to send back."
    )


class SupervisorStep(BaseModel):
    """One step the supervisor chose, as the audit log records it: codes, counts and amounts."""

    model_config = ConfigDict(extra="forbid")

    step: int
    action: SupervisorAction
    reason: SupervisorReason
    stages: list[ReviewedStage] = Field(default_factory=list)
    outcome: StepOutcome
    refusal: StepRefusal | None = None
    agents: list[str] = Field(default_factory=list)  # specialist agents the step ran
    cost_usd: float  # the claim's cost so far, after the step
    seconds: float  # wall-clock time so far, after the step


class TriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    # None when the pipeline stopped before intake finished (see stopped_reason).
    extraction: IntakeExtraction | None
    # None when the pipeline stopped before that step (see stopped_reason).
    coverage: CoverageAssessment | None
    risk: RiskAssessment | None
    recommendation: Recommendation
    routing: RoutingDecision
    agent_calls: list[AgentCall]
    critic_reviews: list[CriticReview] = Field(default_factory=list)
    revision_history: list[RevisionRecord] = Field(default_factory=list)
    # Deterministic fixes made by code from the claim text, e.g. a policy number intake missed.
    code_corrections: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    cost_usd: float = 0.0  # every model call for this claim, at list price, cache-aware
    redactions: dict[str, int] = Field(default_factory=dict)  # identifiers removed, by type
    injection_signals: list[str] = Field(default_factory=list)  # text aimed at the AI
    revisions: int = 0
    critic_unresolved: bool = False
    stopped_reason: str | None = None
    # "agentic": a supervisor agent chose the steps, within code's bounds (ADR 0028); "fixed":
    # code ran them in a fixed order. Results written before the supervisor say "fixed".
    mode: Literal["agentic", "fixed"] = "fixed"
    supervisor_steps: list[SupervisorStep] = Field(default_factory=list)


# --- Ground truth ---------------------------------------------------------


class ExpectedCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: CoverageDecision
    applicable_clauses: list[str] = Field(
        description="The clauses that decide the outcome. Scored on recall."
    )
    covered_amount: float
    reasoning: str


class ExpectedRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: RiskLevel
    flags: list[RiskFlag]


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    scenario: str
    extraction: IntakeExtraction
    coverage: ExpectedCoverage
    risk: ExpectedRisk
    next_step: NextStep
    route: Route
