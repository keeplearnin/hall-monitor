"""Claim triage: intake, policy lookup, coverage check, risk and fraud, critic, recommendation.

The critic reviews intake, coverage and risk against the claim documents and sends work back
for correction, up to MAX_REVISIONS times. Every revision is kept, and a critic-driven change to
a field that drives the decision sends the claim to an adjuster, as does disagreement that
survives the revisions. A claim whose policy can't be found, or that fails for any reason
(refusal, API error, invalid output), stops and goes to an adjuster.

Agents read policy records and claims history only through the governed MCP tools, via the
tool gateway, which enforces each agent's allow-list and records every call. The policy wording
is reference text and is passed in directly.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel, ValidationError

from hall_monitor import tracing
from hall_monitor.agents.base import AgentRefusedError, AgentRun, ToolAccess, render_feedback
from hall_monitor.agents.coverage_check import run_coverage_check
from hall_monitor.agents.critic import run_critic
from hall_monitor.agents.intake import run_intake
from hall_monitor.agents.recommendation import run_recommendation
from hall_monitor.agents.risk_fraud import run_risk_fraud
from hall_monitor.gateway.cost import call_cost
from hall_monitor.gateway.injection import detect_instructions
from hall_monitor.gateway.redaction import redact_claim
from hall_monitor.governance.routing import route_claim, route_stopped
from hall_monitor.models import (
    AgentCall,
    ClaimSubmission,
    CoverageAssessment,
    CriticReview,
    IntakeExtraction,
    NextStep,
    PolicyRecord,
    Recommendation,
    ReviewedStage,
    RevisionRecord,
    RiskAssessment,
    ToolCall,
    TriageResult,
)
from hall_monitor.registry import Registry, RegistryError
from hall_monitor.tools.gateway import ToolGateway

MAX_REVISIONS = 2

# Wayfarer policy numbers: plan code and six digits, e.g. WTP-ESS-100231.
POLICY_NUMBER_PATTERN = re.compile(r"\bWTP-(?:ESS|PLS)-\d{6}\b")

# Fields whose change by a revision can change the outcome, so a person must see it.
DECISION_FIELDS: dict[ReviewedStage, tuple[str, ...]] = {
    ReviewedStage.INTAKE: ("policy_number", "claim_type", "incident_date", "total_claimed"),
    ReviewedStage.COVERAGE_CHECK: ("decision", "covered_amount"),
    ReviewedStage.RISK_FRAUD: ("level", "flags"),
}


class PipelineStopped(Exception):
    pass


@dataclass
class _Run:
    """Everything recorded so far, so a claim that stops part-way keeps its audit trail."""

    calls: list[AgentCall] = field(default_factory=list)
    extraction: IntakeExtraction | None = None
    reviews: list[CriticReview] = field(default_factory=list)
    history: list[RevisionRecord] = field(default_factory=list)
    code_corrections: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    revisions: int = 0

    def output[T](self, run: AgentRun[T]) -> T:
        self.calls.append(run.call)
        return run.output

    def revised[T: BaseModel](self, stage: ReviewedStage, before: T, after: T) -> T:
        old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
        changed = [f for f in DECISION_FIELDS[stage] if old.get(f) != new.get(f)]
        self.history.append(
            RevisionRecord(
                stage=stage, round=self.revisions, before=old, after=new, changed_fields=changed
            )
        )
        return after

    def critic_changed(self) -> list[str]:
        return sorted({f"{r.stage.value}.{f}" for r in self.history for f in r.changed_fields})


def _cost(run: _Run) -> float:
    return round(sum(call_cost(c) for c in run.calls), 6)


def _lookup(extraction: IntakeExtraction, gateway: ToolGateway, run: _Run) -> PolicyRecord | None:
    return gateway.lookup_policy(extraction.policy_number, run.tool_calls)


def _policy_numbers_in(claim: ClaimSubmission, gateway: ToolGateway, run: _Run) -> list[str]:
    """Policy numbers in the claim text that match a real policy record."""
    text = "\n".join([claim.narrative, *(a.text for a in claim.attachments)]).upper()
    candidates = sorted(set(POLICY_NUMBER_PATTERN.findall(text)))
    return [n for n in candidates if gateway.lookup_policy(n, run.tool_calls) is not None]


def _problems(review: CriticReview) -> dict[ReviewedStage, list[str]]:
    by_stage: dict[ReviewedStage, list[str]] = defaultdict(list)
    for issue in review.issues:
        by_stage[issue.stage].append(f"{issue.problem} Evidence: {issue.evidence}")
    return by_stage


def _economy(registry: Registry, agent_id: str, extraction: IntakeExtraction, on: bool) -> bool:
    """Model routing: the cheaper approved model for claims within the manifest's limit."""
    manifest = registry.require(agent_id)
    return (
        on
        and manifest.economy_model is not None
        and extraction.total_claimed <= manifest.economy_max_claim_usd
    )


def _feedback(
    problems: dict[ReviewedStage, list[str]], stage: ReviewedStage, previous: BaseModel
) -> str | None:
    return render_feedback(previous, problems[stage]) if stage in problems else None


@tracing.observe(name="triage", as_type="chain")
def triage(
    claim: ClaimSubmission,
    *,
    registry: Registry,
    wording: str,
    gateway: ToolGateway | None = None,
    client: anthropic.Anthropic | None = None,
    critic: bool = True,
    model_routing: bool = False,
) -> TriageResult:
    """model_routing=False (the default) runs every agent on its main model."""
    client = client or anthropic.Anthropic()
    gateway = gateway or ToolGateway(registry)
    run = _Run()
    # Code checks before any model sees the claim: text aimed at the AI is flagged (and routes
    # the claim to a person), and identifiers no agent needs are removed.
    signals = detect_instructions(claim)
    claim, redactions = redact_claim(claim)
    try:
        result = _triage(
            claim, run, registry, gateway, wording, client, critic, model_routing, signals
        )
        return result.model_copy(
            update={"cost_usd": _cost(run), "redactions": redactions, "injection_signals": signals}
        )
    except AgentRefusedError as exc:
        run.calls.append(exc.call)
        reason = f"agent refused or returned no usable output: {exc}"
    except (anthropic.APIError, ValidationError) as exc:
        reason = f"{type(exc).__name__} from the model call"
    except RegistryError as exc:
        reason = f"hall pass refused: {exc}"
    except PipelineStopped as exc:
        reason = str(exc)
    return TriageResult(
        claim_id=claim.claim_id,
        extraction=run.extraction,
        coverage=None,
        risk=None,
        recommendation=Recommendation(
            next_step=NextStep.REQUEST_INFORMATION, reasons=[reason[:1].upper() + reason[1:] + "."]
        ),
        routing=route_stopped(reason),
        agent_calls=run.calls,
        critic_reviews=run.reviews,
        revision_history=run.history,
        code_corrections=run.code_corrections,
        tool_calls=run.tool_calls,
        revisions=run.revisions,
        stopped_reason=reason,
        cost_usd=_cost(run),
        redactions=redactions,
        injection_signals=signals,
    )


def _triage(
    claim: ClaimSubmission,
    run: _Run,
    registry: Registry,
    gateway: ToolGateway,
    wording: str,
    client: anthropic.Anthropic,
    critic: bool,
    model_routing: bool,
    injection_signals: list[str],
) -> TriageResult:
    # Intake, then a code check that the policy exists. One retry with the problem spelled out.
    run.extraction = run.output(run_intake(claim, registry, client))
    policy = _lookup(run.extraction, gateway, run)
    if policy is None:
        # Code first: if the claim text contains exactly one real policy number, use it.
        found = _policy_numbers_in(claim, gateway, run)
        if len(found) == 1:
            run.code_corrections.append(
                f"policy_number {run.extraction.policy_number!r} replaced by {found[0]!r}, "
                "the only valid policy number in the claim text"
            )
            run.extraction = run.extraction.model_copy(update={"policy_number": found[0]})
            policy = _lookup(run.extraction, gateway, run)
    if policy is None:
        problem = (
            f"No policy record matches policy_number {run.extraction.policy_number!r}. Find the "
            "policy number in the claim and copy it exactly."
        )
        feedback = render_feedback(run.extraction, [problem])
        run.extraction = run.output(run_intake(claim, registry, client, feedback=feedback))
        policy = _lookup(run.extraction, gateway, run)
    if policy is None:
        raise PipelineStopped("no policy record found for the policy number on the claim")
    tools = ToolAccess(gateway, run.tool_calls, scope_policy=policy.policy_number)

    economy = {
        agent: _economy(registry, agent, run.extraction, model_routing)
        for agent in ("coverage_check", "recommendation")
    }
    coverage: CoverageAssessment = run.output(
        run_coverage_check(
            claim,
            run.extraction,
            wording,
            registry,
            client,
            tools=tools,
            economy=economy["coverage_check"],
        )
    )
    risk: RiskAssessment = run.output(
        run_risk_fraud(claim, run.extraction, registry, client, tools=tools)
    )

    unresolved = False
    while critic:
        review = run.output(
            run_critic(
                claim, run.extraction, coverage, risk, wording, registry, client, tools=tools
            )
        )
        run.reviews.append(review)
        if not review.issues:
            break
        if run.revisions == MAX_REVISIONS:
            unresolved = True
            break
        run.revisions += 1
        problems = _problems(review)

        if ReviewedStage.INTAKE in problems:
            previous = run.extraction
            run.extraction = run.revised(
                ReviewedStage.INTAKE,
                previous,
                run.output(
                    run_intake(
                        claim,
                        registry,
                        client,
                        feedback=_feedback(problems, ReviewedStage.INTAKE, previous),
                    )
                ),
            )
            policy = _lookup(run.extraction, gateway, run)
            if policy is None:
                raise PipelineStopped("no policy record found after the critic's correction")
            tools.scope_policy = policy.policy_number
        if ReviewedStage.INTAKE in problems or ReviewedStage.COVERAGE_CHECK in problems:
            # Coverage depends on the extraction, so an intake correction reruns it too.
            coverage = run.revised(
                ReviewedStage.COVERAGE_CHECK,
                coverage,
                run.output(
                    run_coverage_check(
                        claim,
                        run.extraction,
                        wording,
                        registry,
                        client,
                        feedback=_feedback(problems, ReviewedStage.COVERAGE_CHECK, coverage),
                        tools=tools,
                    )
                ),
            )
        if ReviewedStage.INTAKE in problems or ReviewedStage.RISK_FRAUD in problems:
            risk = run.revised(
                ReviewedStage.RISK_FRAUD,
                risk,
                run.output(
                    run_risk_fraud(
                        claim,
                        run.extraction,
                        registry,
                        client,
                        feedback=_feedback(problems, ReviewedStage.RISK_FRAUD, risk),
                        tools=tools,
                    )
                ),
            )

    recommendation = run.output(
        run_recommendation(
            claim.claim_id,
            run.extraction,
            coverage,
            risk,
            registry,
            client,
            economy=economy["recommendation"],
        )
    )
    routing = route_claim(
        run.extraction,
        coverage,
        risk,
        recommendation,
        critic_unresolved=unresolved,
        critic_changed=run.critic_changed(),
        injection_signals=injection_signals,
    )
    return TriageResult(
        claim_id=claim.claim_id,
        extraction=run.extraction,
        coverage=coverage,
        risk=risk,
        recommendation=recommendation,
        routing=routing,
        agent_calls=run.calls,
        critic_reviews=run.reviews,
        revision_history=run.history,
        code_corrections=run.code_corrections,
        tool_calls=run.tool_calls,
        revisions=run.revisions,
        critic_unresolved=unresolved,
    )
