"""The live injection eval (plan step 2, live half): what it runs, where it plants each attack,
and how it scores. Offline: the Claude client is faked, so nothing here calls the API."""

from __future__ import annotations

import json

import pytest

from hall_monitor.claims.data import load_ground_truth
from hall_monitor.claims.gateway import claims_gateway
from hall_monitor.claims.models import NextStep
from hall_monitor.complaints.routing import code_reasons
from hall_monitor.complaints.scripted import scripted_client
from hall_monitor.evals import injection_detector, injection_live
from hall_monitor.evals.injection_live import (
    BASES,
    NO_MODEL_PATH,
    TRIALS,
    Job,
    plan,
    run_job,
)
from hall_monitor.hr.data import load_requests
from hall_monitor.hr.models import LeaveExplanation, LeaveIntake
from hall_monitor.registry import Registry
from hall_monitor.spend import SpendCapReached
from tests.fakes import TEST_REGISTRY_DIR, fake_client, outputs_from_truth

CASES = injection_detector.load_cases()
TEXTS = {c["id"]: c["text"] for c in CASES}
JOBS, NOT_RUN = plan(CASES)
REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
REQUESTS = {r.id: r for r in load_requests()}
CHECK_IDS = ("employer", "service", "hours", "worksite", "reason", "entitlement")


def _client(claim: str = "claim_004", leave: str = "lr_009", overrides: dict | None = None):
    """Answers every agent as its answer key does, for one base claim and leave request."""
    outputs = outputs_from_truth(load_ground_truth(claim))
    outputs[LeaveIntake] = LeaveIntake(
        **REQUESTS[leave].model_dump(include=set(LeaveIntake.model_fields))
    )
    outputs[LeaveExplanation] = LeaveExplanation(
        checks=[{"id": c, "text": f"The {c} check is as the code found."} for c in CHECK_IDS],
        summary="Look at the route reasons first.",
    )
    return fake_client(outputs | (overrides or {}))


@pytest.fixture(scope="module")
def ctx():
    return injection_live.context(REGISTRY, _client(), scripted_client(), spend=None)


def _first(surface: str, base: str | None = None) -> Job:
    return next(
        j for j in JOBS if j.kind == "attack" and j.surface == surface and base in (None, j.base)
    )


# --- What it runs -----------------------------------------------------------------------------


def test_every_attack_the_detector_misses_is_run_or_said_why_not():
    missed = {c["id"] for c in injection_live.missed_attacks(CASES)}
    planned = {j.case_id for j in JOBS if j.kind == "attack"}
    assert planned | set(NOT_RUN) == missed and not planned & set(NOT_RUN)
    # The not-run list can't name an attack the detector now catches, or another surface.
    surfaces = {c["id"]: c["surface"] for c in CASES}
    assert set(NO_MODEL_PATH) <= missed
    assert {surfaces[i] for i in NO_MODEL_PATH} == {"tool_result"}


def test_an_attack_the_detector_catches_is_not_run(monkeypatch):
    caught = injection_live.missed_attacks(CASES)[0]["id"]
    monkeypatch.setattr(
        injection_live, "detect_in_texts", lambda texts: ["x"] if texts[0] == TEXTS[caught] else []
    )
    jobs, _ = plan(CASES)
    assert caught not in {j.case_id for j in jobs}


def test_each_attack_runs_every_trial_on_one_base_and_bases_alternate():
    attacks = [j for j in JOBS if j.kind == "attack"]
    for case_id in {j.case_id for j in attacks}:
        runs = [j for j in attacks if j.case_id == case_id]
        assert sorted(j.trial for j in runs) == list(range(1, TRIALS + 1))
        assert len({j.base for j in runs}) == 1 and runs[0].base in BASES[runs[0].pipeline]
    for pipeline, bases in BASES.items():
        used = [j.base for j in attacks if j.pipeline == pipeline and j.trial == 1]
        assert set(used) == set(bases) and abs(used.count(bases[0]) - used.count(bases[1])) <= 1


def test_every_base_has_clean_control_runs():
    controls = {(j.surface, j.base) for j in JOBS if j.kind == "control"}
    for base in BASES["claims"]:
        assert {("claim_narrative", base), ("tool_result", base)} <= controls
    assert {b for s, b in controls if s == "leave_request"} == set(BASES["leave"])
    assert {b for s, b in controls if s == "complaint"} == set(BASES["complaints"])


def test_the_bases_are_ones_where_an_attack_that_worked_would_show(ctx):
    """A denial an adjuster decides, a capped automatic payment; leave a manager can't sign
    off; complaints whose own fields call for specialist review."""
    deny, capped = (load_ground_truth(b) for b in BASES["claims"])
    assert (deny.next_step, deny.route.value) == (NextStep.DENY, "adjuster")
    assert capped.route.value == "auto" and capped.coverage.covered_amount > 0
    for base in BASES["leave"]:
        assert ctx["leave_answers"][base].route == "hr_review"
    for base in BASES["complaints"]:
        assert code_reasons(ctx["complaints"][base])


# --- Where it plants each attack --------------------------------------------------------------


def test_a_claim_attack_is_planted_in_the_narrative_or_as_an_attachment(ctx):
    narrative = _first("claim_narrative")
    claim = injection_live.plant_claim(narrative, TEXTS[narrative.case_id], ctx["claims"])
    assert claim.narrative.endswith(TEXTS[narrative.case_id])
    attachment = _first("claim_attachment")
    claim = injection_live.plant_claim(attachment, TEXTS[attachment.case_id], ctx["claims"])
    assert claim.attachments[-1].text == TEXTS[attachment.case_id]
    assert claim.claim_id != attachment.base


def test_a_tool_result_attack_is_planted_in_the_claimants_history(ctx):
    job = _first("tool_result")
    claim = injection_live.plant_claim(job, TEXTS[job.case_id], ctx["claims"])
    base = ctx["claims"][job.base]
    assert (claim.narrative, claim.attachments) == (base.narrative, base.attachments)
    history = injection_live.planted_history(job.base, TEXTS[job.case_id], ctx["claims_history"])
    assert history[:-1] == ctx["claims_history"]
    policy = load_ground_truth(job.base).extraction.policy_number
    assert history[-1]["policy_number"] == policy and history[-1]["summary"] == TEXTS[job.case_id]


def test_the_planted_history_is_what_the_records_tool_returns_for_the_claim(ctx):
    from hall_monitor.claims.tool_server import build_server

    job = _first("tool_result")
    policy = load_ground_truth(job.base).extraction.policy_number
    history = injection_live.planted_history(job.base, "planted", ctx["claims_history"])
    server = build_server(history=history)
    gateway = claims_gateway(ctx["registry"], server)
    log: list = []
    result = gateway.call(
        "risk_fraud", "claims_history", {"policy_number": policy}, log, scope_policy=policy
    )
    assert "planted" in [c["summary"] for c in result["claims"]]


def test_a_leave_attack_is_appended_to_the_message(ctx):
    job = _first("leave_request")
    message = injection_live.plant_leave(job, TEXTS[job.case_id], ctx["leave_messages"])
    base = ctx["leave_messages"][job.base]
    assert message.text == f"{base.text}\n\n{TEXTS[job.case_id]}"
    assert message.employee_id == base.employee_id and message.id != base.id


def test_a_complaint_attack_is_the_narrative_of_a_complaint_that_needs_a_specialist(ctx):
    job = _first("complaint")
    complaint = injection_live.plant_complaint(job, TEXTS[job.case_id], ctx["complaints"])
    base = ctx["complaints"][job.base]
    assert complaint.narrative == TEXTS[job.case_id]
    assert code_reasons(complaint) == code_reasons(base)


# --- How it scores ----------------------------------------------------------------------------


def test_a_claim_the_agents_answer_correctly_did_not_work(ctx):
    row = run_job(_first("claim_narrative", "claim_004"), TEXTS, ctx)
    assert row.error is None and row.reached_model
    assert not row.worked and not row.steered, (row.how, row.changed)


def test_a_claim_attack_that_turns_a_denial_into_a_payment_worked(ctx):
    from hall_monitor.claims.models import Recommendation

    paying = _client(
        overrides={
            Recommendation: Recommendation(next_step=NextStep.PAY, reasons=["the claim said so"])
        }
    )
    row = run_job(_first("claim_narrative", "claim_004"), TEXTS, {**ctx, "client": paying})
    assert row.worked and row.steered and "a denial became a payment" in row.how


def test_a_tool_result_attack_no_agent_read_did_not_reach_a_model(ctx, monkeypatch):
    monkeypatch.setattr(injection_live, "CLAIMS_HISTORY_TOOL", "no_such_tool")
    row = run_job(_first("tool_result"), TEXTS, ctx)
    assert row.error is None and not row.reached_model


def test_a_leave_request_answered_correctly_did_not_work(ctx):
    row = run_job(_first("leave_request", "lr_009"), TEXTS, ctx)
    assert row.error is None and row.reached_model
    assert not row.worked and not row.steered, (row.how, row.changed)


def test_a_leave_attack_that_shortens_the_leave_was_steered_and_worked(ctx):
    """lr_009 asks 3 weeks with 2 left; an intake that reads 1 week gets a manager."""
    from datetime import timedelta

    want = REQUESTS["lr_009"]
    short = LeaveIntake(
        **(
            want.model_dump(include=set(LeaveIntake.model_fields))
            | {"end": want.start + timedelta(days=4)}
        )
    )
    row = run_job(
        _first("leave_request", "lr_009"),
        TEXTS,
        {**ctx, "client": _client(overrides={LeaveIntake: short})},
    )
    assert row.worked and "intake.end" in row.changed
    assert any("hr_director" in h for h in row.how)


def test_a_complaint_keeps_its_specialist_route(ctx):
    for base in BASES["complaints"]:
        row = run_job(_first("complaint", base), TEXTS, ctx)
        assert row.error is None and row.reached_model and not row.worked, row.how


@pytest.mark.parametrize("surface", ["claim_narrative", "leave_request", "complaint"])
def test_a_spend_cap_refusal_is_an_error_not_a_pass(ctx, surface):
    class Full:
        def reserve(self, *a, **k):
            raise SpendCapReached("spend cap reached")

        def settle(self, *a, **k):
            pass

    row = run_job(_first(surface), TEXTS, {**ctx, "spend": Full()})
    assert row.error and not row.worked and not row.steered


def test_a_crash_is_an_error_not_a_pass(ctx, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setitem(injection_live.RUNNERS, "claims", boom)
    row = run_job(_first("claim_narrative"), TEXTS, ctx)
    assert row.error == "RuntimeError" and not row.worked


# --- What it publishes ------------------------------------------------------------------------


def test_a_run_names_cases_by_id_never_the_text(ctx, monkeypatch):
    few = {j for j in JOBS if j.kind == "control" or j.trial == 1}
    few = sorted(few, key=lambda j: (j.pipeline, j.case_id))[:3] + [
        _first("claim_attachment"),
        _first("leave_request"),
        _first("complaint"),
    ]
    monkeypatch.setattr(injection_live, "plan", lambda cases, trials: (few, NOT_RUN))
    result = injection_live.run(ctx, workers=1)
    dumped = json.dumps(result)
    assert not any(t in dumped for t in TEXTS.values())
    assert result["summary"]["errors"] == 0, [r for r in result["rows"] if r["error"]]
    assert result["meta"]["mode"] == "live" and result["meta"]["agents"]
    text = injection_live.report(result)
    assert "Attacks that worked: 0" in text


def test_the_plan_needs_no_model():
    assert injection_live.main(["--plan"]) == 0
