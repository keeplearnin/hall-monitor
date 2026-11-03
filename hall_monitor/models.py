"""Typed shapes for claims, agent outputs and ground truth."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ClaimType(StrEnum):
    TRIP_CANCELLATION = "trip_cancellation"
    BAGGAGE_DELAY = "baggage_delay"
    BAGGAGE_LOSS = "baggage_loss"
    EMERGENCY_MEDICAL = "emergency_medical"


class Plan(StrEnum):
    ESSENTIALS = "essentials"
    PLUS = "plus"


# --- Inputs ---------------------------------------------------------------


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    kind: str = Field(description="e.g. receipt, medical_report, carrier_letter, invoice")
    text: str = Field(description="Text content of the document (OCR output in production).")


class ClaimSubmission(BaseModel):
    """What the customer sent. Everything here is untrusted input."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    submitted_at: datetime
    channel: str
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
    amount: float = Field(description="Amount claimed for this item, in the claim currency.")


class IntakeExtraction(BaseModel):
    """Output of the intake agent. Fields not stated in the submission must be null."""

    model_config = ConfigDict(extra="forbid")

    claimant_name: str
    policy_number: str | None
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
    total_claimed: float
    currency: str = Field(description="ISO 4217 code, e.g. USD.")
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
    covered_amount: float = Field(description="What the policy pays in USD. 0 if not covered.")
    reasoning: str


class CoverageAssessment(BaseModel):
    """A coverage finding plus the decision label, which follows from the amount."""

    model_config = ConfigDict(extra="forbid")

    decision: CoverageDecision
    applicable_clauses: list[str]
    covered_amount: float
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


class AgentCall(BaseModel):
    """One agent step: the hall pass it ran under and what it cost."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    agent_version: str
    model: str
    manifest_digest: str
    prompt_version: str
    # What exactly ran: hashes of the rendered system prompt and input, and Anthropic's request ID.
    system_prompt_sha256: str
    input_sha256: str
    request_id: str | None
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    seconds: float


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
    revisions: int = 0
    critic_unresolved: bool = False
    stopped_reason: str | None = None


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
