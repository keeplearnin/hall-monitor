"""HM-22: the leave agents work inside the same controls, and can't change the code's result."""

import typing
from datetime import date

import pytest

from hall_monitor.agents.base import escape
from hall_monitor.governance.audit import AuditLog
from hall_monitor.hr import evals as hr_evals
from hall_monitor.hr.data import (
    load_adversarial,
    load_answers,
    load_company,
    load_employees,
    load_messages,
    load_requests,
)
from hall_monitor.hr.leave_explainer import CARRIES as EXPLAINER_CARRIES
from hall_monitor.hr.leave_intake import CARRIES as INTAKE_CARRIES
from hall_monitor.hr.models import LeaveExplanation, LeaveIntake, Relationship
from hall_monitor.hr.pipeline import prepare
from hall_monitor.hr.routing import PIPELINE_REASONS, REASONS
from hall_monitor.kill_switch import STOPS_FILENAME, add_stop
from hall_monitor.registry import DataClass, Registry
from tests.fakes import TEST_REGISTRY_DIR, fake_client

COMPANY, EMPLOYEES = load_company(), load_employees()
MESSAGES = {m.id: m for m in load_messages()}
REQUESTS = {r.id: r for r in load_requests()}
ANSWERS = load_answers()
CHECK_IDS = ("employer", "service", "hours", "worksite", "reason", "entitlement")


def _intake_for(kwargs) -> LeaveIntake:
    """The structured request the message in this call describes (the answer key)."""
    content = kwargs["messages"][0]["content"]
    # The opening words: identifiers later in a message are redacted before the agent reads it.
    found = next(m for m in MESSAGES.values() if escape(m.text[:60]) in content)
    fields = REQUESTS[found.id].model_dump(include=set(LeaveIntake.model_fields))
    return LeaveIntake(**fields)


def _explanation(kwargs) -> LeaveExplanation:
    return LeaveExplanation(
        checks={c: f"The {c} check is as the code found." for c in CHECK_IDS},
        summary="Look at the route reasons first.",
    )


def _client(overrides: dict | None = None):
    answers = {LeaveIntake: _intake_for, LeaveExplanation: _explanation} | (overrides or {})
    return fake_client(answers)


@pytest.fixture
def registry():
    return Registry.from_dir(TEST_REGISTRY_DIR)


def _prepare(message, registry, client=None, audit=None, employees=EMPLOYEES):
    return prepare(
        message,
        employees=employees,
        company=COMPANY,
        registry=registry,
        client=client or _client(),
        audit=audit,
    )


# --- With answers the rules can check, the pipeline gets the answer key's result -------------


@pytest.mark.parametrize("message", list(MESSAGES.values()), ids=lambda m: m.id)
def test_each_message_reaches_the_answer_key_s_result(message, registry):
    prepared = _prepare(message, registry)
    answer = ANSWERS[message.id]
    assert prepared.stopped_reason is None
    assert prepared.route.route == answer.route
    assert prepared.route.decided_by == answer.decided_by
    assert prepared.route.reasons == answer.reasons
    assert prepared.explanation is not None
    assert [c.agent_id for c in prepared.agent_calls] == ["leave_intake", "leave_explainer"]


def test_every_message_has_a_structured_request_and_an_answer():
    assert set(MESSAGES) == set(REQUESTS) == set(ANSWERS)


def test_the_eval_scores_a_perfect_intake_as_perfect(registry):
    result = hr_evals.run(registry, _client())
    assert result["summary"]["intake_field_accuracy"] == 1.0
    assert result["summary"]["route_accuracy"] == 1.0
    assert result["summary"]["errors"] == 0


def test_the_eval_catches_an_intake_error_the_rules_can_t_see(registry):
    def parent_not_in_law(kwargs):
        intake = _intake_for(kwargs)
        if intake.family_relationship and intake.family_relationship.value == "parent_in_law":
            return intake.model_copy(update={"family_relationship": Relationship.PARENT})
        return intake

    result = hr_evals.run(registry, _client({LeaveIntake: parent_not_in_law}))
    wrong = [r for r in result["runs"] if not r["score"]["route_matches"]]
    assert [r["request_id"] for r in wrong] == ["lr_018"]
    assert result["summary"]["intake_field_accuracy"] < 1.0


# --- Text aimed at the AI goes to a person, whatever the agents say --------------------------


@pytest.mark.parametrize("case", load_adversarial(), ids=lambda c: c[0].id)
def test_text_aimed_at_the_ai_sends_the_request_to_a_person(case, registry):
    message, expected = case
    clean = MESSAGES["lr_001"]

    def clean_intake(kwargs):
        return _intake_for({"messages": [{"content": escape(clean.text)}]})

    prepared = _prepare(message, registry, _client({LeaveIntake: clean_intake}))
    assert prepared.injection_signals == sorted(expected)
    assert prepared.route.route == "hr_review"
    assert "text_addresses_ai" in prepared.route.reasons


# --- What each agent may see -----------------------------------------------------------------


def test_identifiers_are_redacted_before_the_intake_agent_reads_the_message(registry):
    client = _client({LeaveIntake: lambda kw: _intake_for_text(kw, "lr_005")})
    prepared = _prepare(MESSAGES["lr_005"], registry, client)
    sent = client.messages.calls[0]["messages"][0]["content"]
    assert "123-45-6789" not in sent
    assert prepared.redactions and sum(prepared.redactions.values()) == 1


def _intake_for_text(kwargs, request_id):
    fields = REQUESTS[request_id].model_dump(include=set(LeaveIntake.model_fields))
    return LeaveIntake(**fields)


def test_the_explanation_agent_never_sees_the_message_or_who_it_is_from(registry):
    client = _client()
    message = MESSAGES["lr_001"]
    _prepare(message, registry, client)
    explainer = client.messages.calls[1]["messages"][0]["content"]
    assert "knee" not in explainer and "Dana" not in explainer
    assert message.employee_id not in explainer and message.id not in explainer


def test_each_agent_s_hall_pass_allows_exactly_the_data_it_is_sent(registry):
    assert set(registry.get("leave_intake").data_classes) == INTAKE_CARRIES
    assert set(registry.get("leave_explainer").data_classes) == EXPLAINER_CARRIES
    assert DataClass.PII not in EXPLAINER_CARRIES


def test_the_intake_answer_has_no_field_free_text_could_go_in():
    """No diagnosis or condition can travel downstream: every field is a date, a code or a yes
    or no."""
    for name, field in LeaveIntake.model_fields.items():
        types = typing.get_args(field.annotation) or (field.annotation,)
        assert str not in types, name


# --- Any failure stops the request and sends it to a person ----------------------------------


def test_an_intake_refusal_stops_the_request(registry):
    from types import SimpleNamespace

    from tests.fakes import FakeMessages

    client = SimpleNamespace(
        messages=FakeMessages({LeaveIntake: _intake_for}, {LeaveIntake: "refusal"})
    )
    prepared = _prepare(MESSAGES["lr_001"], registry, client)
    assert prepared.route.reasons == ["pipeline_stopped"] and prepared.assessment is None
    assert "declined" in prepared.stopped_reason and len(prepared.agent_calls) == 1


def test_a_stopped_intake_agent_stops_the_request(tmp_path):
    import shutil

    shutil.copytree(TEST_REGISTRY_DIR, tmp_path / "registry")
    add_stop(tmp_path / "registry" / STOPS_FILENAME, agent="leave_intake", by="own-401", reason="x")
    prepared = _prepare(MESSAGES["lr_001"], Registry.from_dir(tmp_path / "registry"))
    assert prepared.route.reasons == ["pipeline_stopped"]
    assert prepared.stopped_reason.startswith("hall pass refused")
    assert prepared.agent_calls == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"start": None}, "the request doesn't give its dates"),
        ({"end": date(2026, 11, 1)}, "the extracted request isn't valid"),
    ],
)
def test_an_unusable_extraction_goes_to_a_person(change, reason, registry):
    def broken(kwargs):
        return _intake_for(kwargs).model_copy(update=change)

    prepared = _prepare(MESSAGES["lr_001"], registry, _client({LeaveIntake: broken}))
    assert prepared.route.reasons == ["facts_missing"] and prepared.stopped_reason.startswith(
        reason
    )


def test_a_request_for_an_unknown_employee_goes_to_a_person(registry):
    prepared = _prepare(MESSAGES["lr_001"], registry, employees={})
    assert prepared.route.reasons == ["facts_missing"] and prepared.assessment is None


def test_without_a_usable_explanation_the_request_goes_to_the_same_person(registry):
    def wrong_checks(kwargs):
        return LeaveExplanation(checks={"employer": "fine"}, summary="ok")

    baseline = _prepare(MESSAGES["lr_009"], registry)
    prepared = _prepare(MESSAGES["lr_009"], registry, _client({LeaveExplanation: wrong_checks}))
    assert prepared.explanation is None and "exactly the checks" in prepared.explanation_problem
    assert prepared.route == baseline.route


def test_an_explanation_refusal_leaves_the_route_as_the_code_set_it(registry):
    from types import SimpleNamespace

    from tests.fakes import FakeMessages

    client = SimpleNamespace(
        messages=FakeMessages(
            {LeaveIntake: _intake_for, LeaveExplanation: _explanation},
            {LeaveExplanation: "refusal"},
        )
    )
    prepared = _prepare(MESSAGES["lr_012"], registry, client)
    assert prepared.explanation is None and prepared.explanation_problem
    assert (
        prepared.route.reasons == ["retaliation_risk"]
        and prepared.route.decided_by == "hr_director"
    )


# --- The audit trail -------------------------------------------------------------------------


def test_each_step_is_logged_with_nothing_the_employee_or_a_model_wrote(registry, tmp_path):
    log = AuditLog(tmp_path / "audit.sqlite")
    message = MESSAGES["lr_005"]
    _prepare(message, registry, audit=log)
    entries = log.entries(message.id)
    assert [e["event"] for e in entries] == [
        "leave_request_submitted",
        "agent_call",
        "leave_assessed",
        "agent_call",
        "leave_prepared",
    ]
    logged = str([e["payload"] for e in entries])
    for text in ("treatment", "123-45-6789", "2026-11-30", "Look at the route reasons"):
        assert text not in logged, text
    assert log.verify().ok


def test_a_stopped_request_is_logged_too(registry, tmp_path):
    log = AuditLog(tmp_path / "audit.sqlite")
    _prepare(MESSAGES["lr_001"], registry, audit=log, employees={})
    events = [e["event"] for e in log.entries("lr_001")]
    assert events == ["leave_request_submitted", "agent_call", "leave_prepared"]
    assert log.entries("lr_001")[-1]["payload"]["reasons"] == ["facts_missing"]


def test_pipeline_reasons_are_routing_reasons():
    assert PIPELINE_REASONS <= set(REASONS)
