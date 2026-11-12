"""The agentic claims path: a supervisor agent picks the steps, code holds the boundaries.

ADR 0028, HM-32. The supervisor (agents/supervisor.py) chooses one action at a time from a
closed list. Before anything runs, code checks, in this order:

1. **Budget.** Steps, cost and wall-clock time for the claim. Running out stops the claim for a
   person. Steps are checked before every supervisor turn, finish included; cost and time
   before every model call the loop makes (the supervisor's and each specialist's, each agent
   of a revision included), so the overshoot is at most one agent's run with its tool loop.
2. **Kill switch and hall pass.** The supervisor's own hall pass is checked when it is asked
   and again before the step it chose runs, so stopping the supervisor stops its work at the
   next step; each specialist checks its own on every model and tool call (agents/base.py).
3. **The choice.** A step whose inputs aren't ready is refused, and so is one whose inputs
   haven't changed since it last ran (a repeat), a revision of a stage the critic didn't raise,
   or a revision past MAX_REVISIONS. A refusal runs nothing and costs a step; after
   MAX_REFUSALS the claim stops for a person.

Code still does what the fixed path did in code: the policy check after intake, the one
correction from the claim text, the decision label from the amount, rerunning coverage and
risk after an intake revision, and the record of every revision. Finishing is the
supervisor's request, not its decision: code routes the claim, and auto is possible only if
every required step ran on the latest inputs (intake with a policy found, coverage, risk, the
critic when it is on, recommendation) and route_claim, unchanged, finds nothing for a person.
A hand-off always goes to a person.

Every choice is recorded as it happens (``supervisor_step``): the action, its reason code,
the stages, what code did with it, the agents it ran and the cost and time so far. No free text.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import anthropic

from hall_monitor.agents.base import ToolAccess, render_feedback
from hall_monitor.agents.coverage_check import run_coverage_check
from hall_monitor.agents.critic import run_critic
from hall_monitor.agents.intake import run_intake
from hall_monitor.agents.recommendation import run_recommendation
from hall_monitor.agents.risk_fraud import run_risk_fraud
from hall_monitor.agents.supervisor import (
    AGENT_ID as SUPERVISOR,
)
from hall_monitor.agents.supervisor import (
    SupervisorView,
    _Budget,
    _Coverage,
    _Critic,
    _Intake,
    _LastStep,
    _Recommendation,
    _Risk,
    run_supervisor,
)
from hall_monitor.amounts import source_text
from hall_monitor.governance.routing import route_claim
from hall_monitor.models import (
    ClaimSubmission,
    CoverageAssessment,
    CriticReview,
    NextStep,
    PolicyRecord,
    Recommendation,
    ReviewedStage,
    RiskAssessment,
    Route,
    RoutingDecision,
    StepOutcome,
    StepRefusal,
    SupervisorAction,
    SupervisorDecision,
    SupervisorStep,
    TriageResult,
)
from hall_monitor.pipeline import (
    MAX_REVISIONS,
    PipelineStopped,
    _cost,
    _economy,
    _feedback,
    _lookup,
    _policy_numbers_in,
    _problems,
    _Run,
)
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway

MAX_REFUSALS = 3
STAGE_ORDER = (ReviewedStage.INTAKE, ReviewedStage.COVERAGE_CHECK, ReviewedStage.RISK_FRAUD)


@dataclass(frozen=True)
class Limits:
    """A claim's budget. The largest published claim cost about 0.05 USD over five agent calls;
    the old order with two revision rounds takes 10 supervisor steps (11 with an intake retry),
    and every refused step counts too."""

    max_steps: int = 16
    max_cost_usd: float = 0.25
    max_seconds: float = 300.0


@dataclass
class _State:
    """The claim's work so far, with what each result was computed from. A result is current
    only if its inputs haven't changed since: that is what the required-evidence check reads."""

    intake_runs: int = 0
    policy: PolicyRecord | None = None
    extraction_v: int = 0
    coverage: CoverageAssessment | None = None
    coverage_v: int = 0
    coverage_on: int = -1  # extraction_v it was computed from
    risk: RiskAssessment | None = None
    risk_v: int = 0
    risk_on: int = -1
    review: CriticReview | None = None
    review_on: tuple[int, int, int] | None = None
    recommendation: Recommendation | None = None
    recommendation_on: tuple[int, int, int] | None = None
    tools: ToolAccess | None = None
    refusals: int = 0
    last: SupervisorStep | None = None
    agents: list[str] = field(default_factory=list)

    def inputs(self) -> tuple[int, int, int]:
        return (self.extraction_v, self.coverage_v, self.risk_v)

    def coverage_current(self) -> bool:
        return self.coverage is not None and self.coverage_on == self.extraction_v

    def risk_current(self) -> bool:
        return self.risk is not None and self.risk_on == self.extraction_v

    def review_current(self) -> bool:
        return self.review is not None and self.review_on == self.inputs()

    def recommendation_current(self) -> bool:
        return self.recommendation is not None and self.recommendation_on == self.inputs()


def _status(present: bool, current: bool) -> str:
    return "missing" if not present else ("current" if current else "out_of_date")


@dataclass
class _Context:
    claim: ClaimSubmission
    run: _Run
    registry: Registry
    gateway: ToolGateway
    wording: str
    client: anthropic.Anthropic
    critic: bool
    model_routing: bool
    injection_signals: list[str]
    limits: Limits
    clock: Callable[[], float]
    started: float = 0.0

    def elapsed(self) -> float:
        return self.clock() - self.started


def view_of(ctx: _Context, state: _State) -> SupervisorView:
    extraction = ctx.run.extraction
    last = state.last
    return SupervisorView(
        critic_enabled=ctx.critic,
        injection_signals=len(ctx.injection_signals),
        intake=_Intake(
            runs=state.intake_runs,
            has_extraction=extraction is not None,
            policy_found=state.policy is not None,
            claim_type=extraction.claim_type if extraction else None,
            currency_is_usd=extraction.currency == "USD" if extraction else None,
            total_claimed=extraction.total_claimed if extraction else None,
        ),
        coverage=_Coverage(
            status=_status(state.coverage is not None, state.coverage_current()),
            decision=state.coverage.decision if state.coverage else None,
            covered_amount=state.coverage.covered_amount if state.coverage else None,
        ),
        risk=_Risk(
            status=_status(state.risk is not None, state.risk_current()),
            level=state.risk.level if state.risk else None,
            flags=list(state.risk.flags) if state.risk else [],
        ),
        critic=_Critic(
            status=_status(state.review is not None, state.review_current()),
            reviews=len(ctx.run.reviews),
            issue_stages=sorted(
                {i.stage for i in state.review.issues} if state.review else set(),
                key=STAGE_ORDER.index,
            ),
        ),
        revisions_used=ctx.run.revisions,
        max_revisions=MAX_REVISIONS,
        recommendation=_Recommendation(
            status=_status(state.recommendation is not None, state.recommendation_current()),
            next_step=state.recommendation.next_step if state.recommendation else None,
        ),
        last_step=None
        if last is None
        else _LastStep(action=last.action, outcome=last.outcome, refusal=last.refusal),
        budget_left=_Budget(
            steps=max(ctx.limits.max_steps - len(ctx.run.steps), 0),
            cost_usd=round(max(ctx.limits.max_cost_usd - _cost(ctx.run), 0.0), 6),
            seconds=round(max(ctx.limits.max_seconds - ctx.elapsed(), 0.0), 1),
        ),
    )


def _check_budget(ctx: _Context) -> None:
    if len(ctx.run.steps) >= ctx.limits.max_steps:
        raise PipelineStopped(f"budget exhausted: {ctx.limits.max_steps} supervisor steps")
    _check_spend(ctx)


def _check_spend(ctx: _Context) -> None:
    """Cost and time, checked before every model call the loop makes: the supervisor's and each
    specialist's, including each agent inside one revision."""
    if _cost(ctx.run) >= ctx.limits.max_cost_usd:
        raise PipelineStopped(f"budget exhausted: cost reached {ctx.limits.max_cost_usd} USD")
    if ctx.elapsed() >= ctx.limits.max_seconds:
        raise PipelineStopped(f"budget exhausted: {ctx.limits.max_seconds:g} seconds")


def _refusal(ctx: _Context, state: _State, decision: SupervisorDecision) -> StepRefusal | None:
    """Why code won't run the choice, or None if it may run."""
    A = SupervisorAction
    match decision.action:
        case A.RUN_INTAKE:
            if ctx.run.extraction is None:
                return None
            if state.policy is None and state.intake_runs < 2:
                return None
            return StepRefusal.REPEAT_WITHOUT_CHANGE
        case A.RUN_COVERAGE_CHECK | A.RUN_RISK_FRAUD:
            if state.policy is None:
                return StepRefusal.PRECONDITION_UNMET
            current = (
                state.coverage_current()
                if decision.action is A.RUN_COVERAGE_CHECK
                else state.risk_current()
            )
            return StepRefusal.REPEAT_WITHOUT_CHANGE if current else None
        case A.RUN_CRITIC:
            if not ctx.critic:
                return StepRefusal.CRITIC_OFF
            if not (state.coverage_current() and state.risk_current()):
                return StepRefusal.PRECONDITION_UNMET
            return StepRefusal.REPEAT_WITHOUT_CHANGE if state.review_current() else None
        case A.REVISE:
            if not ctx.critic:
                return StepRefusal.CRITIC_OFF
            if not (state.review_current() and state.review.issues):
                return StepRefusal.NO_CRITIC_ISSUES
            if ctx.run.revisions >= MAX_REVISIONS:
                return StepRefusal.REVISIONS_EXHAUSTED
            raised = {i.stage for i in state.review.issues}
            if not decision.stages or not set(decision.stages) <= raised:
                return StepRefusal.STAGE_WITHOUT_ISSUE
            if set(decision.stages) != raised:
                # Every raised stage goes back, as on the fixed path: an issue left out could
                # vanish from a later clean review and never be recorded (review 22).
                return StepRefusal.NOT_EVERY_RAISED_STAGE
            return None
        case A.RUN_RECOMMENDATION:
            if not (state.coverage_current() and state.risk_current()):
                return StepRefusal.PRECONDITION_UNMET
            if ctx.critic and not state.review_current():
                return StepRefusal.PRECONDITION_UNMET
            return StepRefusal.REPEAT_WITHOUT_CHANGE if state.recommendation_current() else None
        case A.FINISH | A.HAND_OFF:
            return None


def _run_intake(ctx: _Context, state: _State) -> None:
    run, claim = ctx.run, ctx.claim
    feedback = None
    if run.extraction is not None and state.policy is None:
        problem = (
            f"No policy record matches policy_number {run.extraction.policy_number!r}. Find the "
            "policy number in the claim and copy it exactly."
        )
        feedback = render_feedback(run.extraction, [problem])
    state.intake_runs += 1
    _check_spend(ctx)
    state.agents.append("intake")
    run.extraction = run.output(run_intake(claim, ctx.registry, ctx.client, feedback=feedback))
    state.extraction_v += 1
    state.policy = _lookup(run.extraction, ctx.gateway, run)
    if state.policy is None and feedback is None:
        # Code first, as on the fixed path: one real policy number in the claim text is used.
        found = _policy_numbers_in(claim, ctx.gateway, run)
        if len(found) == 1:
            run.code_corrections.append(
                f"policy_number {run.extraction.policy_number!r} replaced by {found[0]!r}, "
                "the only valid policy number in the claim text"
            )
            run.extraction = run.extraction.model_copy(update={"policy_number": found[0]})
            state.policy = _lookup(run.extraction, ctx.gateway, run)
    if state.policy is None and state.intake_runs >= 2:
        raise PipelineStopped("no policy record found for the policy number on the claim")
    if state.policy is not None:
        if state.tools is None:
            state.tools = ToolAccess(
                ctx.gateway, run.tool_calls, scope_policy=state.policy.policy_number
            )
        else:
            state.tools.scope_policy = state.policy.policy_number


def _run_coverage(
    ctx: _Context, state: _State, feedback: str | None = None, *, revision: bool = False
) -> CoverageAssessment:
    _check_spend(ctx)
    state.agents.append("coverage_check")
    economy = (
        not revision
        and state.coverage is None
        and _economy(ctx.registry, "coverage_check", ctx.run.extraction, ctx.model_routing)
    )
    coverage = ctx.run.output(
        run_coverage_check(
            ctx.claim,
            ctx.run.extraction,
            ctx.wording,
            ctx.registry,
            ctx.client,
            feedback=feedback,
            tools=state.tools,
            economy=economy,
        )
    )
    state.coverage_v += 1
    state.coverage_on = state.extraction_v
    return coverage


def _run_risk(ctx: _Context, state: _State, feedback: str | None = None) -> RiskAssessment:
    _check_spend(ctx)
    state.agents.append("risk_fraud")
    risk = ctx.run.output(
        run_risk_fraud(
            ctx.claim,
            ctx.run.extraction,
            ctx.registry,
            ctx.client,
            feedback=feedback,
            tools=state.tools,
        )
    )
    state.risk_v += 1
    state.risk_on = state.extraction_v
    return risk


def _revise(ctx: _Context, state: _State, stages: list[ReviewedStage]) -> None:
    """One revision round, as on the fixed path: the stages chosen get the critic's problems
    as feedback, and an intake revision reruns coverage and risk too (they read intake)."""
    run = ctx.run
    run.revisions += 1
    problems = _problems(state.review)
    chosen = set(stages)
    if ReviewedStage.INTAKE in chosen:
        chosen |= {ReviewedStage.COVERAGE_CHECK, ReviewedStage.RISK_FRAUD}
        previous = run.extraction
        _check_spend(ctx)
        state.agents.append("intake")
        run.extraction = run.revised(
            ReviewedStage.INTAKE,
            previous,
            run.output(
                run_intake(
                    ctx.claim,
                    ctx.registry,
                    ctx.client,
                    feedback=_feedback(problems, ReviewedStage.INTAKE, previous),
                )
            ),
        )
        state.extraction_v += 1
        state.policy = _lookup(run.extraction, ctx.gateway, run)
        if state.policy is None:
            raise PipelineStopped("no policy record found after the critic's correction")
        state.tools.scope_policy = state.policy.policy_number
    if ReviewedStage.COVERAGE_CHECK in chosen:
        state.coverage = run.revised(
            ReviewedStage.COVERAGE_CHECK,
            state.coverage,
            _run_coverage(
                ctx,
                state,
                _feedback(problems, ReviewedStage.COVERAGE_CHECK, state.coverage),
                revision=True,
            ),
        )
    if ReviewedStage.RISK_FRAUD in chosen:
        state.risk = run.revised(
            ReviewedStage.RISK_FRAUD,
            state.risk,
            _run_risk(ctx, state, _feedback(problems, ReviewedStage.RISK_FRAUD, state.risk)),
        )


def _execute(ctx: _Context, state: _State, decision: SupervisorDecision) -> None:
    A = SupervisorAction
    run = ctx.run
    match decision.action:
        case A.RUN_INTAKE:
            _run_intake(ctx, state)
        case A.RUN_COVERAGE_CHECK:
            state.coverage = _run_coverage(ctx, state)
        case A.RUN_RISK_FRAUD:
            state.risk = _run_risk(ctx, state)
        case A.RUN_CRITIC:
            _check_spend(ctx)
            state.agents.append("critic")
            review = run.output(
                run_critic(
                    ctx.claim,
                    run.extraction,
                    state.coverage,
                    state.risk,
                    ctx.wording,
                    ctx.registry,
                    ctx.client,
                    tools=state.tools,
                )
            )
            run.reviews.append(review)
            state.review, state.review_on = review, state.inputs()
        case A.REVISE:
            _revise(ctx, state, decision.stages)
        case A.RUN_RECOMMENDATION:
            _check_spend(ctx)
            state.agents.append("recommendation")
            economy = _economy(ctx.registry, "recommendation", run.extraction, ctx.model_routing)
            state.recommendation = run.output(
                run_recommendation(
                    ctx.claim.claim_id,
                    run.extraction,
                    state.coverage,
                    state.risk,
                    ctx.registry,
                    ctx.client,
                    economy=economy,
                )
            )
            state.recommendation_on = state.inputs()


def _record(
    ctx: _Context,
    state: _State,
    decision: SupervisorDecision,
    outcome: StepOutcome,
    refusal: StepRefusal | None = None,
) -> None:
    step = SupervisorStep(
        step=len(ctx.run.steps) + 1,
        action=decision.action,
        reason=decision.reason,
        stages=decision.stages if decision.action is SupervisorAction.REVISE else [],
        outcome=outcome,
        refusal=refusal,
        agents=list(state.agents),
        cost_usd=_cost(ctx.run),
        seconds=round(ctx.elapsed(), 2),
    )
    state.agents = []
    state.last = step
    ctx.run.steps.append(step)


def missing_evidence(state: _State, *, critic: bool, has_extraction: bool) -> list[str]:
    """Required steps that didn't run, or ran on inputs that have changed since."""
    missing = []
    if not has_extraction or state.policy is None:
        missing.append("intake with a policy found")
    if not state.coverage_current():
        missing.append("coverage_check")
    if not state.risk_current():
        missing.append("risk_fraud")
    if critic and not state.review_current():
        missing.append("critic")
    if not state.recommendation_current():
        missing.append("recommendation")
    return missing


def _recheck(ctx: _Context) -> None:
    """Every agent the result rests on, the supervisor included, must still be allowed to run."""
    for agent_id in dict.fromkeys(call.agent_id for call in ctx.run.calls):
        ctx.registry.require(agent_id)


def _result(ctx: _Context, state: _State, decision: SupervisorDecision) -> TriageResult:
    run = ctx.run
    missing = missing_evidence(state, critic=ctx.critic, has_extraction=run.extraction is not None)
    unresolved = ctx.critic and state.review_current() and bool(state.review.issues)
    reasons: list[str] = []
    if decision.action is SupervisorAction.HAND_OFF:
        reasons.append(f"supervisor handed the claim to a person ({decision.reason.value})")
    if missing:
        reasons.append("required steps missing or out of date: " + ", ".join(missing))
        routing = RoutingDecision(route=Route.ADJUSTER, reasons=reasons)
    else:
        routing = route_claim(
            run.extraction,
            state.coverage,
            state.risk,
            state.recommendation,
            critic_unresolved=unresolved,
            critic_changed=run.critic_changed(),
            injection_signals=ctx.injection_signals,
            policy=state.policy,
            claim_text=source_text(ctx.claim),
        )
        if reasons:
            routing = RoutingDecision(route=Route.ADJUSTER, reasons=reasons + routing.reasons)
    recommendation = (
        state.recommendation
        if state.recommendation_current()
        else Recommendation(
            next_step=NextStep.REQUEST_INFORMATION,
            reasons=["No current recommendation: a person decides."],
        )
    )
    return TriageResult(
        claim_id=ctx.claim.claim_id,
        extraction=run.extraction,
        coverage=state.coverage,
        risk=state.risk,
        recommendation=recommendation,
        routing=routing,
        agent_calls=run.calls,
        critic_reviews=run.reviews,
        revision_history=run.history,
        code_corrections=run.code_corrections,
        tool_calls=run.tool_calls,
        revisions=run.revisions,
        critic_unresolved=unresolved,
        mode="agentic",
        supervisor_steps=run.steps,
    )


def supervise(
    claim: ClaimSubmission,
    run: _Run,
    registry: Registry,
    gateway: ToolGateway,
    wording: str,
    client: anthropic.Anthropic,
    critic: bool,
    model_routing: bool,
    injection_signals: list[str],
    limits: Limits,
    clock: Callable[[], float] = time.monotonic,
) -> TriageResult:
    ctx = _Context(
        claim,
        run,
        registry,
        gateway,
        wording,
        client,
        critic,
        model_routing,
        injection_signals,
        limits,
        clock,
    )
    ctx.started = clock()
    state = _State()
    while True:
        _check_budget(ctx)
        decision = run.output(run_supervisor(claim.claim_id, view_of(ctx, state), registry, client))
        if decision.action in (SupervisorAction.FINISH, SupervisorAction.HAND_OFF):
            try:
                _recheck(ctx)
            except Exception:
                # A stop between the choice and routing: the trail says the step failed, not
                # that it finished (review 22).
                _record(ctx, state, decision, StepOutcome.FAILED)
                raise
            done = decision.action is SupervisorAction.FINISH
            _record(ctx, state, decision, StepOutcome.FINISHED if done else StepOutcome.HANDED_OFF)
            return _result(ctx, state, decision)
        refusal = _refusal(ctx, state, decision)
        if refusal is not None:
            state.refusals += 1
            _record(ctx, state, decision, StepOutcome.REFUSED, refusal)
            if state.refusals >= MAX_REFUSALS:
                raise PipelineStopped(f"supervisor loop: {MAX_REFUSALS} steps refused")
            continue
        # The step was the supervisor's choice: a stop made since it chose ends it here.
        registry.require(SUPERVISOR)
        try:
            _execute(ctx, state, decision)
        except Exception:
            # Recorded even when the step failed part-way, so the path shows where it stopped.
            _record(ctx, state, decision, StepOutcome.FAILED)
            raise
        _record(ctx, state, decision, StepOutcome.RAN)
