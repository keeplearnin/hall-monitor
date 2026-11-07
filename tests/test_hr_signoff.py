"""HM-23: a person decides each leave request, signed, within the role the rules name."""

import sqlite3

import pytest

from hall_monitor.governance import signing
from hall_monitor.governance.audit import AuditLog
from hall_monitor.governance.signing import demo_private_key
from hall_monitor.hr import review as review_cli
from hall_monitor.hr.data import load_company, load_employees, load_messages
from hall_monitor.hr.pipeline import prepare
from hall_monitor.hr.signoff import (
    DecisionRefused,
    LeaveDecision,
    decide,
    evidence,
    evidence_pack,
    load_staff,
    status,
)
from hall_monitor.registry import Registry
from tests.fakes import TEST_REGISTRY_DIR
from tests.test_hr_agents import _client

COMPANY, EMPLOYEES = load_company(), load_employees()
MESSAGES = {m.id: m for m in load_messages()}
STAFF = load_staff()
REASON = "Checked the request and the records against the policy."


@pytest.fixture
def log(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite")
    registry = Registry.from_dir(TEST_REGISTRY_DIR)
    for message in MESSAGES.values():
        prepare(
            message,
            employees=EMPLOYEES,
            company=COMPANY,
            registry=registry,
            client=_client(),
            audit=audit,
        )
    yield audit
    audit.close()


def _decision(log, request_id, staff_id, outcome, weeks=0.0, reason="", digest=None, key=None):
    digest = digest or evidence(log, request_id)[0]
    decision = LeaveDecision(
        staff_id=staff_id, outcome=outcome, weeks=weeks, reason=reason, evidence_sha256=digest
    )
    return decision.signed(request_id, key or demo_private_key(staff_id))


def _decide(log, request_id, staff_id, outcome, **kw):
    return decide(log, request_id, _decision(log, request_id, staff_id, outcome, **kw), STAFF)


# --- Who may decide what ---------------------------------------------------------------------


def test_a_specialist_signs_off_a_clean_request(log):
    final = _decide(log, "lr_001", "hrs-101", "approve", weeks=4)
    assert final["outcome"] == "approve" and final["role"] == "leave_specialist"
    assert status(log, "lr_001")["state"] == "decided"


def test_a_request_is_decided_once(log):
    _decide(log, "lr_001", "hrs-101", "approve", weeks=4)
    with pytest.raises(DecisionRefused, match="already decided"):
        _decide(log, "lr_001", "hrs-201", "deny", reason=REASON)


def test_the_role_the_rules_name_is_enforced(log):
    # lr_002: short service, an HR manager's case; lr_012: retaliation risk, a director's.
    with pytest.raises(DecisionRefused, match="needs a hr_manager or above"):
        _decide(log, "lr_002", "hrs-101", "deny", reason=REASON)
    with pytest.raises(DecisionRefused, match="needs a hr_director or above"):
        _decide(log, "lr_012", "hrs-201", "approve", weeks=4, reason=REASON)
    assert _decide(log, "lr_002", "hrs-201", "deny", reason=REASON)["outcome"] == "deny"
    assert _decide(log, "lr_012", "hrs-301", "approve", weeks=4, reason=REASON)["role"] == (
        "hr_director"
    )


def test_only_a_manager_or_above_may_deny_even_on_a_specialist_case(log):
    with pytest.raises(DecisionRefused, match="HR manager or above"):
        _decide(log, "lr_013", "hrs-101", "deny", reason=REASON)


def test_fmla_leave_is_approved_only_for_an_eligible_employee_with_a_qualifying_reason(log):
    for request_id in ("lr_002", "lr_018"):  # short service; a parent-in-law
        with pytest.raises(DecisionRefused, match="eligible employee with a qualifying reason"):
            _decide(log, request_id, "hrs-301", "approve", weeks=4, reason=REASON)


def test_approved_weeks_fit_what_is_left_and_what_was_asked(log):
    # lr_009: 2 weeks left, 3 asked, a director's case.
    with pytest.raises(DecisionRefused, match="no more than are left"):
        _decide(log, "lr_009", "hrs-301", "approve", weeks=3, reason=REASON)
    assert _decide(log, "lr_009", "hrs-301", "approve", weeks=2, reason=REASON)["weeks"] == 2
    with pytest.raises(DecisionRefused, match="more than were asked for"):
        _decide(log, "lr_001", "hrs-101", "approve", weeks=4.5)


def test_only_a_director_may_extend_past_the_entitlement(log):
    with pytest.raises(DecisionRefused, match="hr_director"):
        _decide(log, "lr_009", "hrs-201", "extend", weeks=1, reason=REASON)
    assert _decide(log, "lr_009", "hrs-301", "extend", weeks=3, reason=REASON)["outcome"] == (
        "extend"
    )


@pytest.mark.parametrize(
    ("request_id", "staff", "outcome", "weeks"),
    [
        ("lr_002", "hrs-201", "deny", 0),
        ("lr_009", "hrs-301", "extend", 1),
        ("lr_013", "hrs-101", "approve", 4),
    ],
    ids=["deny", "extend", "approve a request sent to HR review"],
)
def test_a_reason_is_needed_to_deny_extend_or_approve_against_the_route(
    log, request_id, staff, outcome, weeks
):
    with pytest.raises(DecisionRefused, match="reason of at least 20 characters"):
        _decide(log, request_id, staff, outcome, weeks=weeks, reason="ok")


def test_anyone_registered_may_ask_for_more_information(log):
    asked = _decide(log, "lr_012", "hrs-101", "request_information")
    assert asked["outcome"] == "request_information"


# --- Signatures and evidence -----------------------------------------------------------------


def test_an_unsigned_or_wrongly_signed_decision_is_refused_and_logged(log):
    with pytest.raises(DecisionRefused, match="signed with hrs-101's key"):
        _decide(log, "lr_001", "hrs-101", "approve", weeks=4, key=demo_private_key("hrs-201"))
    refusal = log.entries("lr_001")[-1]
    assert refusal["event"] == "leave_decision_refused" and refusal["actor"] == "hr:hrs-101"
    assert status(log, "lr_001")["refused_decisions"] == 1


def test_a_changed_decision_breaks_its_signature(log):
    decision = _decision(log, "lr_001", "hrs-101", "approve", weeks=2)
    with pytest.raises(DecisionRefused, match="signed"):
        decide(log, "lr_001", decision.model_copy(update={"weeks": 4}), STAFF)


def test_someone_who_isn_t_registered_is_refused(log):
    with pytest.raises(DecisionRefused, match="isn't registered"):
        _decide(log, "lr_001", "hrs-999", "approve", weeks=4)
    assert log.entries("lr_001")[-1]["actor"] == "hr:?"


def test_a_decision_on_other_evidence_is_refused(log):
    with pytest.raises(DecisionRefused, match="evidence hash"):
        _decide(log, "lr_001", "hrs-101", "approve", weeks=4, digest="0" * 64)


def test_changed_evidence_is_refused_and_nothing_is_recorded(log):
    digest = evidence(log, "lr_001")[0]
    db = sqlite3.connect(log.path)
    db.execute("DROP TRIGGER evidence_no_update")
    db.execute(
        "UPDATE evidence SET body = replace(body, 'approve', 'deny') WHERE sha256 = ?", (digest,)
    )
    db.execute("UPDATE evidence SET body = body || ' ' WHERE sha256 = ?", (digest,))
    db.commit()
    db.close()
    with pytest.raises(DecisionRefused, match="changed after it was stored"):
        _decide(log, "lr_001", "hrs-101", "approve", weeks=4, digest=digest)
    assert not log.has("lr_001", "leave_decision")
    assert log.entries("lr_001")[-1]["event"] == "leave_decision_refused"


def test_a_request_is_prepared_once(log):
    with pytest.raises(ValueError, match="already prepared"):
        prepare(
            MESSAGES["lr_001"],
            employees=EMPLOYEES,
            company=COMPANY,
            registry=Registry.from_dir(TEST_REGISTRY_DIR),
            client=_client(),
            audit=log,
        )


def test_two_decisions_at_once_record_one(log, monkeypatch):
    """The once-only append decides a race the first check didn't see."""
    first = _decision(log, "lr_001", "hrs-101", "approve", weeks=4)
    second = _decision(log, "lr_001", "hrs-301", "approve", weeks=3, reason=REASON)
    decide(log, "lr_001", first, STAFF)
    monkeypatch.setattr(log, "has", lambda *a: False)
    with pytest.raises(DecisionRefused, match="already decided"):
        decide(log, "lr_001", second, STAFF)
    decisions = [e for e in log.entries("lr_001") if e["event"] == "leave_decision"]
    assert len(decisions) == 1 and decisions[0]["actor"] == "hr:hrs-101"


def test_identical_packs_have_different_hashes():
    from hall_monitor.governance.approval import evidence_sha256

    prepared = type("P", (), {})()
    for name, value in {
        "request_id": "lr_x",
        "employee_id": "e1",
        "request": None,
        "assessment": None,
        "route": type("R", (), {"model_dump": lambda self: {}})(),
        "explanation": None,
        "explanation_problem": None,
        "injection_signals": [],
        "stopped_reason": "x",
        "message_redacted": "m",
    }.items():
        setattr(prepared, name, value)
    assert evidence_sha256(evidence_pack(prepared)) != evidence_sha256(evidence_pack(prepared))


# --- What the log keeps ----------------------------------------------------------------------


def test_the_log_keeps_no_reason_and_no_signature_but_the_store_can_prove_them(log):
    decision = _decision(log, "lr_002", "hrs-201", "deny", reason=REASON)
    decide(log, "lr_002", decision, STAFF)
    logged = log.entries("lr_002")[-1]["payload"]
    assert REASON not in str(logged) and decision.signature not in str(logged)
    assert logged["reason_hmac"] == log.keyed_hash(REASON)
    stored = log.get_evidence(
        next(
            row[0]
            for row in log._db.execute("SELECT sha256 FROM evidence").fetchall()
            if log.keyed_hash(row[0]) == logged["decision_hmac"]
        )
    )
    message = signing.decision_message(
        "lr_002", {k: v for k, v in stored.items() if k != "request_id"}
    )
    assert signing.verify(message, stored["signature"], STAFF["hrs-201"].public_key)
    assert log.verify().ok


def test_a_stopped_preparation_still_waits_for_a_person(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite")
    prepare(
        MESSAGES["lr_001"],
        employees={},
        company=COMPANY,
        registry=Registry.from_dir(TEST_REGISTRY_DIR),
        client=_client(),
        audit=audit,
    )
    assert status(audit, "lr_001")["state"] == "waiting for HR"
    assert status(audit, "lr_001")["reasons"] == ["facts_missing"]


# --- The CLI ---------------------------------------------------------------------------------


def test_the_review_cli_lists_shows_and_decides(log, capsys):
    import json

    db = ["--audit-db", str(log.path)]
    assert review_cli.main([*db, "list"]) == 0
    waiting = json.loads(capsys.readouterr().out)["waiting_for_hr"]
    assert len(waiting) == 28
    assert review_cli.main([*db, "show", "lr_001"]) == 0
    shown = json.loads(capsys.readouterr().out)
    args = [*db, "decide", "lr_001", "--staff", "hrs-101", "--outcome", "approve", "--weeks", "4"]
    assert review_cli.main([*args, "--evidence-sha256", shown["evidence_sha256"]]) == 0
    capsys.readouterr()
    assert review_cli.main([*args, "--evidence-sha256", shown["evidence_sha256"]]) == 2
    assert "already decided" in capsys.readouterr().err
    assert review_cli.main([*db, "show", "lr_999"]) == 1


def test_extending_needs_a_director_even_on_a_specialist_s_case(log):
    with pytest.raises(DecisionRefused, match="extending leave past the entitlement needs"):
        _decide(log, "lr_013", "hrs-201", "extend", weeks=2, reason=REASON)


# --- Review 10 findings ----------------------------------------------------------------------


def test_approving_fewer_weeks_than_asked_is_a_partial_denial(log):
    with pytest.raises(DecisionRefused, match="partial denial"):
        _decide(log, "lr_001", "hrs-101", "approve", weeks=0.01)
    with pytest.raises(DecisionRefused, match="reason of at least"):
        _decide(log, "lr_001", "hrs-201", "approve", weeks=2)
    assert _decide(log, "lr_001", "hrs-201", "approve", weeks=2, reason=REASON)["weeks"] == 2


def test_asking_for_information_leaves_the_request_waiting(log):
    asked = _decide(log, "lr_012", "hrs-101", "request_information")
    assert asked["outcome"] == "request_information"
    assert not log.has("lr_012", "leave_decision")
    state = status(log, "lr_012")
    assert state["state"] == "waiting for HR" and state["information_requested"] == 1
    assert _decide(log, "lr_012", "hrs-301", "approve", weeks=4, reason=REASON)["role"] == (
        "hr_director"
    )


@pytest.mark.parametrize(
    ("request_id", "weeks", "match"),
    [
        ("lr_001", 4, "only for leave past the entitlement"),
        ("lr_009", 4, "more than were asked for"),
        ("lr_002", 3, "eligible employee with a qualifying reason"),
        ("lr_009", 0, "needs the weeks it grants"),
    ],
)
def test_an_extension_is_only_past_the_entitlement_and_up_to_what_was_asked(
    log, request_id, weeks, match
):
    with pytest.raises(DecisionRefused, match=match):
        _decide(log, request_id, "hrs-301", "extend", weeks=weeks, reason=REASON)


def test_bonding_weeks_after_the_12_months_can_t_be_approved(log):
    # lr_017: 6 weeks asked; 11 workdays (2.2 weeks) fall before the bonding year ends.
    with pytest.raises(DecisionRefused, match="no more than qualify"):
        _decide(log, "lr_017", "hrs-201", "approve", weeks=6, reason=REASON)
    assert _decide(log, "lr_017", "hrs-201", "approve", weeks=2.2, reason=REASON)["weeks"] == 2.2


def test_the_chain_check_reports_a_changed_or_erased_leave_pack(log):
    changed, erased = evidence(log, "lr_002")[0], evidence(log, "lr_005")[0]
    db = sqlite3.connect(log.path)
    db.execute("DROP TRIGGER evidence_no_update")
    db.execute("UPDATE evidence SET body = body || ' ' WHERE sha256 = ?", (changed,))
    db.execute("DELETE FROM evidence WHERE sha256 = ?", (erased,))
    db.commit()
    db.close()
    result = AuditLog(log.path, read_only=True).verify()
    assert any(changed[:12] in p for p in result.problems)
    assert erased in result.evidence_erased


def test_requests_reach_the_sign_off_through_the_run_command(tmp_path):
    from hall_monitor.hr import run as hr_run

    db = tmp_path / "audit.sqlite"
    prepared = hr_run.run(["lr_001", "lr_009"], db, client=_client())
    assert [p["route"] for p in prepared] == ["specialist_sign_off", "hr_review"]
    audit = AuditLog(db)
    assert status(audit, "lr_009")["state"] == "waiting for HR"
    with pytest.raises(SystemExit):
        hr_run.run(["lr_999"], db, client=_client())


def test_a_losing_preparation_doesn_t_change_what_hr_sees(log, monkeypatch):
    before = status(log, "lr_001")
    monkeypatch.setattr(log, "has", lambda *a: False)
    with pytest.raises(ValueError, match="already prepared"):
        prepare(
            MESSAGES["lr_001"],
            employees={},
            company=COMPANY,
            registry=Registry.from_dir(TEST_REGISTRY_DIR),
            client=_client(),
            audit=log,
        )
    after = status(log, "lr_001")
    assert after["route"] == before["route"] == "specialist_sign_off" and after["reasons"] == []


def test_a_losing_decision_leaves_nothing_in_the_store_and_its_refusal_is_logged(log, monkeypatch):
    first = _decision(log, "lr_001", "hrs-101", "approve", weeks=4)
    second = _decision(
        log, "lr_001", "hrs-201", "approve", weeks=2, reason="A reason only the loser gave."
    )
    decide(log, "lr_001", first, STAFF)
    monkeypatch.setattr(log, "has", lambda *a: False)
    with pytest.raises(DecisionRefused):
        decide(log, "lr_001", second, STAFF)
    bodies = [row[0] for row in log._db.execute("SELECT body FROM evidence").fetchall()]
    assert not any("only the loser gave" in b for b in bodies)
    assert log.entries("lr_001")[-1]["event"] == "leave_decision_refused"


def test_every_refusal_of_a_prepared_request_is_logged(log):
    _decide(log, "lr_001", "hrs-101", "approve", weeks=4)
    with pytest.raises(DecisionRefused):
        _decide(log, "lr_001", "hrs-101", "approve", weeks=4)
    assert log.entries("lr_001")[-1]["event"] == "leave_decision_refused"
    erased = evidence(log, "lr_003")[0]
    log._db.execute("DELETE FROM evidence WHERE sha256 = ?", (erased,))
    with pytest.raises(DecisionRefused, match="no longer stored"):
        _decide(log, "lr_003", "hrs-101", "approve", weeks=4, digest=erased)
    assert log.entries("lr_003")[-1]["event"] == "leave_decision_refused"


@pytest.mark.parametrize(
    ("request_id", "staff", "outcome", "weeks", "reason", "match"),
    [
        ("lr_001", "hrs-101", "approve", 0, "", "more than 0"),
        ("lr_002", "hrs-201", "deny", 1, REASON, "can't carry weeks"),
        ("lr_001", "hrs-101", "request_information", 1, "", "can't carry weeks"),
        ("lr_004", "hrs-301", "approve", 4, REASON, "eligible employee"),  # low hours
        ("lr_006", "hrs-301", "approve", 4, REASON, "eligible employee"),  # small worksite
        ("lr_008", "hrs-301", "approve", 4, REASON, "eligible employee"),  # employer
        ("lr_002", "hrs-201", "deny", 0, " " * 25, "reason of at least"),  # spaces only
    ],
)
def test_each_remaining_rule_refuses(log, request_id, staff, outcome, weeks, reason, match):
    with pytest.raises(DecisionRefused, match=match):
        _decide(log, request_id, staff, outcome, weeks=weeks, reason=reason)


def test_evidence_is_recorded_once_and_a_second_preparation_writes_nothing(log):
    from hall_monitor.hr.signoff import record_evidence

    entries = len(log.entries("lr_001"))
    with pytest.raises(ValueError, match="already prepared"):
        prepare(
            MESSAGES["lr_001"],
            employees=EMPLOYEES,
            company=COMPANY,
            registry=Registry.from_dir(TEST_REGISTRY_DIR),
            client=_client(),
            audit=log,
        )
    assert len(log.entries("lr_001")) == entries
    stub = type("P", (), {"request_id": "lr_001"})()
    for name in (
        "employee_id",
        "request",
        "assessment",
        "explanation",
        "explanation_problem",
        "stopped_reason",
        "message_redacted",
    ):
        setattr(stub, name, None)
    stub.route = type("R", (), {"model_dump": lambda self: {}})()
    stub.injection_signals = []
    with pytest.raises(ValueError, match="already prepared"):
        record_evidence(log, stub)


def test_the_pack_holds_the_redacted_message_for_the_specialist(log):
    pack = evidence(log, "lr_005")[1]
    assert "[SSN REDACTED]" in pack["message"] and "123-45-6789" not in pack["message"]


def test_status_doesn_t_need_the_prepared_entry(log, monkeypatch):
    entries = [e for e in log.entries("lr_001") if e["event"] != "leave_prepared"]
    monkeypatch.setattr(log, "entries", lambda request_id=None: entries)
    assert status(log, "lr_001")["route"] == "specialist_sign_off"


def test_the_cli_refuses_bad_input_without_a_traceback(log, capsys, tmp_path):
    import json

    db = ["--audit-db", str(log.path)]
    review_cli.main([*db, "show", "lr_001"])
    digest = json.loads(capsys.readouterr().out)["evidence_sha256"]
    base = [
        *db,
        "decide",
        "lr_001",
        "--staff",
        "hrs-101",
        "--outcome",
        "approve",
        "--evidence-sha256",
        digest,
    ]
    assert review_cli.main([*base, "--weeks", "60"]) == 2
    err = capsys.readouterr().err
    assert "60" not in err and "weeks" in err
    assert review_cli.main([*base, "--weeks", "4", "--key", str(tmp_path / "missing.pem")]) == 2


@pytest.mark.parametrize(
    "body",
    [
        "staff:\n  - {id: x, name: X, role: boss, public_key: k}\n",
        "staff:\n  - {id: x, name: X, role: hr_manager, public_key: k}\n"
        "  - {id: x, name: Y, role: hr_director, public_key: k}\n",
    ],
    ids=["unknown role", "duplicate id"],
)
def test_a_bad_staff_file_is_refused_naming_the_entry(tmp_path, body):
    bad = tmp_path / "hr_staff.yaml"
    bad.write_text(body)
    with pytest.raises(ValueError, match="x"):
        load_staff(bad)
