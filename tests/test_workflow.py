"""The adjuster approval gate and its saved checkpoints (fake Claude client, no network)."""

import pytest

from hall_monitor.data import iter_claims, load_ground_truth, load_policy_wording
from hall_monitor.governance.approval import AdjusterDecision, build_evidence_pack, evidence_sha256
from hall_monitor.governance.signing import demo_private_key
from hall_monitor.models import NextStep
from hall_monitor.pipeline import triage
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import ToolGateway
from hall_monitor.workflow import open_workflow
from tests.fakes import TEST_REGISTRY_DIR, fake_client, outputs_from_truth, signed_decision

CLAIMS = {c.claim_id: c for c in iter_claims()}
REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
GATEWAY = ToolGateway(REGISTRY)
WORDING = load_policy_wording()


def _open(path, claim_id=None):
    client = fake_client(outputs_from_truth(load_ground_truth(claim_id))) if claim_id else None
    return open_workflow(
        path,
        audit_db=path.with_name("audit.sqlite"),
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=client,
    )


def _submit(path, claim_id):
    with _open(path, claim_id) as workflow:
        return workflow.submit(CLAIMS[claim_id])


def _decide(path, claim_id, **fields):
    with _open(path) as workflow:  # a fresh process: nothing but the saved checkpoint
        status = workflow.status(claim_id)
        if "evidence_sha256" not in fields:
            fields["evidence_sha256"] = status["evidence_sha256"]
        return workflow.decide(claim_id, signed_decision(claim_id, **fields))


@pytest.fixture
def db(tmp_path):
    return tmp_path / "checkpoints.sqlite"


def test_small_clean_claim_is_decided_automatically(db):
    status = _submit(db, "claim_001")
    assert status["state"] == "decided"
    final = status["final"]
    assert final["decided_by"] == "auto" and final["payout_usd"] == 1992.0
    assert len(final["evidence_sha256"]) == 64


def test_claim_waits_for_an_adjuster_across_a_restart(db):
    status = _submit(db, "claim_004")  # a denial
    assert status["state"] == "waiting_for_adjuster"
    assert "nothing covered (a denial)" in status["routing_reasons"]

    with _open(db) as workflow:  # reopened: the claim is still waiting
        assert workflow.waiting() == ["claim_004"]

    status = _decide(
        db,
        "claim_004",
        adjuster_id="adj-101",
        next_step=NextStep.DENY,
        payout_usd=0,
        reason="Medication changed 43 days before purchase: pre-existing (5.1).",
    )
    final = status["final"]
    assert status["state"] == "decided"
    assert final["decided_by"] == "adjuster:adj-101" and final["adjuster_name"] == "Jordan Avery"
    assert final["overrode_ai"] is False
    with _open(db) as workflow:
        assert workflow.waiting() == []


def test_payout_above_authority_needs_a_senior_adjuster(db):
    _submit(db, "claim_003")  # pay 6,480
    accept = {"next_step": NextStep.PAY, "payout_usd": 6480.0}

    status = _decide(db, "claim_003", adjuster_id="adj-101", **accept)
    assert status["state"] == "waiting_for_adjuster"
    assert "authority" in status["previous_decision_rejected"]

    status = _decide(db, "claim_003", adjuster_id="adj-201", **accept)
    assert status["state"] == "decided"
    assert status["final"]["decided_by"] == "adjuster:adj-201"


def test_paying_a_flagged_claim_needs_a_senior_adjuster(db):
    _submit(db, "claim_006")  # pay 100, duplicate receipt flagged
    pay = {"next_step": NextStep.PAY, "payout_usd": 100.0}
    status = _decide(db, "claim_006", adjuster_id="adj-101", **pay)
    assert "senior adjuster" in status["previous_decision_rejected"]
    assert _decide(db, "claim_006", adjuster_id="adj-201", **pay)["state"] == "decided"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"adjuster_id": "adj-999"}, "isn't registered"),
        ({"evidence_sha256": "0" * 64}, "evidence hash"),
        ({"next_step": NextStep.PAY, "payout_usd": 50.0}, "reason"),  # override, no reason
        ({"next_step": NextStep.PAY, "payout_usd": 99_999.0, "reason": "x" * 30}, "exceeds"),
        ({"next_step": NextStep.DENY, "payout_usd": 10.0, "reason": "x" * 30}, "payout"),
    ],
)
def test_decisions_that_break_the_rules_are_rejected(db, fields, message):
    _submit(db, "claim_004")
    decision = {"adjuster_id": "adj-201", "next_step": NextStep.DENY, "payout_usd": 0} | fields
    decision.setdefault("reason", "")
    status = _decide(db, "claim_004", **decision)
    assert status["state"] == "waiting_for_adjuster"
    assert message in status["previous_decision_rejected"]


def test_an_override_with_a_reason_is_recorded_as_one(db):
    _submit(db, "claim_004")
    status = _decide(
        db,
        "claim_004",
        adjuster_id="adj-201",
        next_step=NextStep.PAY,
        payout_usd=500.0,
        reason="Goodwill payment approved by claims manager for the lodge deposit.",
    )
    final = status["final"]
    assert final["overrode_ai"] is True and final["ai_next_step"] == "deny"


def test_evidence_hash_is_stable_and_tracks_content():
    claim = CLAIMS["claim_004"]
    result = triage(
        claim,
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=fake_client(outputs_from_truth(load_ground_truth("claim_004"))),
    )
    pack = build_evidence_pack(result, claim)
    assert evidence_sha256(pack) == evidence_sha256(build_evidence_pack(result, claim))
    pack["coverage"]["covered_amount"] = 1.0
    assert evidence_sha256(pack) != evidence_sha256(build_evidence_pack(result, claim))


def test_a_claim_can_only_be_submitted_once(db):
    _submit(db, "claim_001")
    with pytest.raises(ValueError, match="already"):
        _submit(db, "claim_001")


# --- Signatures and evidence integrity ---------------------------------------------------

DENY = {
    "adjuster_id": "adj-101",
    "next_step": NextStep.DENY,
    "payout_usd": 0,
    "reason": "Medication changed 43 days before purchase: pre-existing (5.1).",
}


@pytest.mark.parametrize(
    "make",
    [
        lambda h: AdjusterDecision(**DENY, evidence_sha256=h),
        lambda h: AdjusterDecision(**DENY, evidence_sha256=h).signed(
            "claim_004", demo_private_key("adj-201")
        ),
        lambda h: AdjusterDecision(**DENY, evidence_sha256=h).signed(
            "claim_003", demo_private_key("adj-101")
        ),
        lambda h: (
            AdjusterDecision(**DENY, evidence_sha256=h)
            .signed("claim_004", demo_private_key("adj-101"))
            .model_copy(update={"reason": "Changed after it was signed, by someone else."})
        ),
    ],
    ids=["unsigned", "someone-elses-key", "signed-for-another-claim", "changed-after-signing"],
)
def test_a_decision_counts_only_with_the_adjusters_signature(db, make):
    _submit(db, "claim_004")
    with _open(db) as workflow:
        digest = workflow.status("claim_004")["evidence_sha256"]
        status = workflow.decide("claim_004", make(digest))
    assert status["state"] == "waiting_for_adjuster"
    assert "isn't signed with adj-101's key" in status["previous_decision_rejected"]


@pytest.mark.parametrize("rehash", [False, True], ids=["pack-edited", "pack-and-hash-edited"])
def test_an_edited_checkpoint_cant_change_the_evidence(db, rehash):
    _submit(db, "claim_004")
    with _open(db) as workflow:
        config = {"configurable": {"thread_id": "claim_004"}}
        pack = workflow.status("claim_004")["evidence"]
        pack["recommendation"]["next_step"] = "pay"  # someone with file access edits it
        update = {"evidence": pack}
        if rehash:
            update["evidence_sha256"] = evidence_sha256(pack)
        workflow.app.update_state(config, update)
        digest = workflow.status("claim_004")["evidence_sha256"]
        decision = AdjusterDecision(**DENY, evidence_sha256=digest)
        status = workflow.decide(
            "claim_004", decision.signed("claim_004", demo_private_key("adj-101"))
        )
    assert status["state"] == "waiting_for_adjuster"
    assert "evidence pack" in status["previous_decision_rejected"]


def test_a_claim_whose_triage_crashed_can_be_restarted_on_request(db, monkeypatch):
    import hall_monitor.workflow as workflow_module

    real = workflow_module.build_evidence_pack

    def crash(*args):
        raise RuntimeError("process died mid-triage")

    monkeypatch.setattr(workflow_module, "build_evidence_pack", crash)
    with pytest.raises(RuntimeError):
        _submit(db, "claim_001")
    with _open(db) as workflow:
        assert workflow.status("claim_001")["state"] == "in_progress"

    monkeypatch.setattr(workflow_module, "build_evidence_pack", real)
    # Not restarted silently: another process might still be triaging it.
    with pytest.raises(ValueError, match="restart"):
        _submit(db, "claim_001")
    with _open(db, "claim_001") as workflow:
        assert workflow.submit(CLAIMS["claim_001"], restart=True)["state"] == "decided"


def test_the_adjuster_is_shown_the_stored_evidence_not_an_edited_checkpoint(db):
    _submit(db, "claim_004")
    with _open(db) as workflow:
        stored = workflow.status("claim_004")["evidence"]
        edited = stored | {"recommendation": {"next_step": "pay", "reasons": []}}
        workflow.app.update_state(
            {"configurable": {"thread_id": "claim_004"}}, {"evidence": edited}
        )
        status = workflow.status("claim_004")
    assert status["evidence"] == stored
    assert "changed after triage" in status["evidence_problem"]


# --- From the whole-repo review (Phase B) -------------------------------------------------


def _run_fresh(db, claim_id, monkeypatch):
    """`run --fresh` as a user runs it, but on the fake client and this test's databases."""
    import hall_monitor.run as run_module
    from hall_monitor.data import DATA_DIR

    real = run_module.open_workflow

    def fake_open(checkpoints, **kwargs):
        client = fake_client(outputs_from_truth(load_ground_truth(claim_id)))
        return real(checkpoints, audit_db=db.with_name("audit.sqlite"), client=client, **kwargs)

    monkeypatch.setattr(run_module, "open_workflow", fake_open)
    claim_file = DATA_DIR / "claims" / f"{claim_id}.json"
    return run_module.main(["--claim", str(claim_file), "--checkpoints", str(db), "--fresh"])


@pytest.mark.parametrize("claim_id", ["claim_001", "claim_004"], ids=["decided", "waiting"])
def test_run_fresh_refuses_a_claim_that_finished_triage(db, monkeypatch, claim_id):
    """--fresh restarts a crashed triage only: a decided claim must not be paid twice, and a
    claim waiting for an adjuster must not be re-run until it routes automatically."""
    _submit(db, claim_id)
    assert _run_fresh(db, claim_id, monkeypatch) != 0  # refused with a message, not a traceback
    with _open(db) as workflow:
        triaged = [e for e in workflow.audit.entries(claim_id) if e["event"] == "triage_result"]
    assert len(triaged) == 1


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r["risk"].update(flags=[], level="low"),
        lambda r: r["extraction"].update(total_claimed=1.0),
    ],
    ids=["fraud-flag-cleared", "amount-lowered"],
)
def test_an_edited_checkpoint_result_cant_relax_the_approval_rules(db, edit):
    """The approval rules read the saved triage result, so it must be the one the log recorded."""
    _submit(db, "claim_006")  # pay 100, duplicate receipt flagged: needs a senior adjuster
    with _open(db) as workflow:
        config = {"configurable": {"thread_id": "claim_006"}}
        result = workflow.app.get_state(config).values["result"]
        edit(result)
        workflow.app.update_state(config, {"result": result})
    status = _decide(
        db, "claim_006", adjuster_id="adj-101", next_step=NextStep.PAY, payout_usd=100.0
    )
    assert status["state"] == "waiting_for_adjuster"
    assert "triage result" in status["previous_decision_rejected"]


def test_a_claim_the_log_shows_triaged_cant_be_triaged_again_with_new_checkpoints(db):
    """The audit log, not the checkpoint file, decides whether a claim finished triage: a new
    or deleted checkpoints file must not let a decided claim be paid again."""
    _submit(db, "claim_001")
    other = db.with_name("other-checkpoints.sqlite")  # same audit log, fresh checkpoints
    with pytest.raises(ValueError, match="audit log"):
        _submit(other, "claim_001")
    with _open(db) as workflow:
        finals = [e for e in workflow.audit.entries("claim_001") if e["event"] == "final_decision"]
    assert len(finals) == 1


def test_a_saved_result_that_no_longer_parses_is_a_recorded_rejection(db):
    _submit(db, "claim_006")
    with _open(db) as workflow:
        config = {"configurable": {"thread_id": "claim_006"}}
        result = workflow.app.get_state(config).values["result"]
        result["risk"]["level"] = "not-a-level"
        workflow.app.update_state(config, {"result": result})
    status = _decide(
        db, "claim_006", adjuster_id="adj-201", next_step=NextStep.PAY, payout_usd=100.0
    )
    assert status["state"] == "waiting_for_adjuster"
    assert "triage result" in status["previous_decision_rejected"]
    with _open(db) as workflow:
        events = [e["event"] for e in workflow.audit.entries("claim_006")]
    assert events[-1] == "decision_rejected"


# --- From review 6 (whole repo after HM-10) --------------------------------------------------


def _finals(db, claim_id):
    with _open(db) as workflow:
        return [e for e in workflow.audit.entries(claim_id) if e["event"] == "final_decision"]


def test_two_runs_of_the_same_claim_at_once_pay_it_once(db, monkeypatch):
    """A queue that redelivers, or a double click: both runs pass submit's checks before
    either finishes triage. Only the first final decision is recorded; the second run fails."""
    import hall_monitor.workflow as workflow_module

    real = workflow_module.build_evidence_pack
    nested = []

    def other_run_finishes_first(*args):
        if not nested:
            nested.append(True)
            with _open(db.with_name("other-checkpoints.sqlite"), "claim_001") as other:
                assert other.submit(CLAIMS["claim_001"])["state"] == "decided"
        return real(*args)

    monkeypatch.setattr(workflow_module, "build_evidence_pack", other_run_finishes_first)
    with pytest.raises(ValueError, match="already"):
        _submit(db, "claim_001")
    assert len(_finals(db, "claim_001")) == 1


def test_two_adjusters_deciding_at_once_decide_once(db, monkeypatch):
    """Both resume the same waiting checkpoint; the second decision is refused and recorded."""
    import hall_monitor.workflow as workflow_module

    _submit(db, "claim_004")
    real = workflow_module.validate_decision
    nested = []

    def other_adjuster_decides_first(*args, **kwargs):
        if not nested:
            nested.append(True)
            status = _decide(
                db,
                "claim_004",
                adjuster_id="adj-201",
                next_step=NextStep.DENY,
                payout_usd=0,
                reason="Medication changed 43 days before purchase: pre-existing (5.1).",
            )
            assert status["state"] == "decided"
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow_module, "validate_decision", other_adjuster_decides_first)
    with pytest.raises(ValueError, match="already decided"):
        _decide(
            db,
            "claim_004",
            adjuster_id="adj-101",
            next_step=NextStep.DENY,
            payout_usd=0,
            reason="Pre-existing condition under clause 5.1; medication change noted.",
        )
    finals = _finals(db, "claim_004")
    assert [f["actor"] for f in finals] == ["adjuster:adj-201"]
    with _open(db) as workflow:
        assert workflow.status("claim_004")["state"] == "decided"
        assert workflow.waiting() == []


def test_a_restored_checkpoints_file_cant_decide_a_claim_again(db):
    """Restoring the checkpoints file from a backup brings back a claim that was waiting; the
    audit log, not the checkpoint, says it has been decided."""
    digest = _submit(db, "claim_003")["evidence_sha256"]
    backup = db.read_bytes()
    _decide(db, "claim_003", adjuster_id="adj-201", next_step=NextStep.PAY, payout_usd=6480.0)
    db.write_bytes(backup)
    with _open(db) as workflow:
        assert workflow.status("claim_003")["state"] == "decided"
        assert workflow.waiting() == []
    with pytest.raises(ValueError, match="already decided"):
        _decide(
            db,
            "claim_003",
            adjuster_id="adj-201",
            next_step=NextStep.PAY,
            payout_usd=10.0,
            evidence_sha256=digest,
        )
    assert len(_finals(db, "claim_003")) == 1


def test_entries_appended_to_the_log_cant_relax_the_approval_rules(db):
    """Someone who can edit the checkpoint can usually append to the audit log beside it. The
    pack and result checked are the ones recorded at triage, not the latest appended."""
    from hall_monitor.governance.audit import AuditLog, sha256
    from hall_monitor.models import TriageResult

    _submit(db, "claim_006")  # duplicate receipt flagged: needs a senior adjuster
    config = {"configurable": {"thread_id": "claim_006"}}
    with _open(db) as workflow:
        values = workflow.app.get_state(config).values
        result, pack = values["result"], values["evidence"]
        result["risk"].update(flags=[], level="low")
        pack["risk"].update(flags=[], level="low")
        workflow.app.update_state(
            config, {"result": result, "evidence": pack, "evidence_sha256": evidence_sha256(pack)}
        )
    log = AuditLog(db.with_name("audit.sqlite"))
    forged = sha256(TriageResult.model_validate(result).model_dump_json())
    log.append("claim_006", "triage_result", "pipeline", {"result_sha256": forged})
    log.append(
        "claim_006", "evidence_pack", "pipeline", {"evidence_sha256": log.put_evidence(pack)}
    )
    log.close()

    with _open(db) as workflow:
        status = workflow.status("claim_006")
        assert status["evidence_problem"]
        decision = signed_decision(
            "claim_006",
            adjuster_id="adj-101",
            next_step=NextStep.PAY,
            payout_usd=100.0,
            evidence_sha256=status["evidence_sha256"],
        )
        assert workflow.decide("claim_006", decision)["state"] == "waiting_for_adjuster"
    assert _finals(db, "claim_006") == []


def test_waiting_works_on_a_fresh_checkpoints_file(db):
    with _open(db) as workflow:
        assert workflow.waiting() == []


def test_a_rejection_logs_the_submission_as_a_keyed_hash(db):
    """An unkeyed hash of the submission could confirm a guessed reason; a keyed one can't."""
    from hall_monitor.governance.audit import canonical, sha256

    _submit(db, "claim_003")
    reason = "Crohn's medication changed 43 days before purchase (5.1)."
    status = _decide(
        db,
        "claim_003",
        adjuster_id="adj-101",
        next_step=NextStep.PAY,
        payout_usd=6480.0,
        reason=reason,
    )
    assert status["state"] == "waiting_for_adjuster"
    with _open(db) as workflow:
        rejected = workflow.audit.entries("claim_003")[-1]
        submitted = signed_decision(
            "claim_003",
            adjuster_id="adj-101",
            next_step=NextStep.PAY,
            payout_usd=6480.0,
            reason=reason,
            evidence_sha256=status["evidence_sha256"],
        ).model_dump(mode="json")
        assert rejected["event"] == "decision_rejected"
        assert "submitted_sha256" not in rejected["payload"]
        assert rejected["payload"]["submitted_hmac"] == workflow.audit.keyed_hash(
            canonical(submitted)
        )
        assert sha256(canonical(submitted)) not in str(rejected["payload"])


def test_a_checkpoint_decision_the_log_doesnt_hold_isnt_reported_decided(db):
    """The log, not the checkpoint, says whether a claim was decided: an edited checkpoint
    can't take a waiting claim out of the queue or show a payout nobody approved."""
    _submit(db, "claim_004")
    config = {"configurable": {"thread_id": "claim_004"}}
    with _open(db) as workflow:
        workflow.app.update_state(config, {"final": {"claim_id": "claim_004", "payout_usd": 99999}})
        status = workflow.status("claim_004")
    assert status["state"] != "decided"
    assert "audit log" in status["problem"]


def test_an_edited_decision_in_the_checkpoint_is_reported(db):
    _submit(db, "claim_001")
    config = {"configurable": {"thread_id": "claim_001"}}
    with _open(db) as workflow:
        final = workflow.app.get_state(config).values["final"]
        workflow.app.update_state(config, {"final": final | {"payout_usd": 99999.0}})
        status = workflow.status("claim_001")
    assert status["state"] == "decided"
    assert status["final"]["payout_usd"] == 1992.0  # the log's, not the edited checkpoint's
    assert "audit log" in status["problem"]


def test_two_runs_of_a_claim_for_review_leave_it_decidable(db, monkeypatch):
    """Two runs of a claim that goes to an adjuster: the second fails, and the first stays
    waiting and can be decided, rather than reading as tampered with."""
    import hall_monitor.workflow as workflow_module

    real = workflow_module.build_evidence_pack
    nested = []

    def other_run_finishes_first(*args):
        if not nested:
            nested.append(True)
            with _open(winner, "claim_004") as other:
                assert other.submit(CLAIMS["claim_004"])["state"] == "waiting_for_adjuster"
        return real(*args)

    winner = db.with_name("other-checkpoints.sqlite")  # another worker, same audit log
    monkeypatch.setattr(workflow_module, "build_evidence_pack", other_run_finishes_first)
    with pytest.raises(ValueError, match="already triaged"):
        _submit(db, "claim_004")
    with _open(winner) as workflow:
        status = workflow.status("claim_004")
    assert status["state"] == "waiting_for_adjuster" and status["evidence_problem"] is None
    decided = _decide(
        winner,
        "claim_004",
        adjuster_id="adj-101",
        next_step=NextStep.DENY,
        payout_usd=0,
        reason="Medication changed 43 days before purchase: pre-existing (5.1).",
    )
    assert decided["state"] == "decided"


def test_the_adjuster_sees_the_pack_stored_at_triage_after_a_forged_append(db):
    from hall_monitor.governance.audit import AuditLog

    stored = _submit(db, "claim_006")["evidence"]
    forged = stored | {"risk": stored["risk"] | {"flags": [], "level": "low"}}
    config = {"configurable": {"thread_id": "claim_006"}}
    log = AuditLog(db.with_name("audit.sqlite"))
    log.append(
        "claim_006", "evidence_pack", "pipeline", {"evidence_sha256": log.put_evidence(forged)}
    )
    log.close()
    with _open(db) as workflow:
        workflow.app.update_state(config, {"evidence": forged})
        status = workflow.status("claim_006")
    assert status["evidence"] == stored
    assert status["evidence_problem"]


def test_cli_reports_a_claim_already_decided_without_a_traceback(db, monkeypatch, capsys):
    import hall_monitor.review as review_module

    digest = _submit(db, "claim_003")["evidence_sha256"]
    backup = db.read_bytes()
    _decide(db, "claim_003", adjuster_id="adj-201", next_step=NextStep.PAY, payout_usd=6480.0)
    db.write_bytes(backup)
    real = review_module.open_workflow
    monkeypatch.setattr(
        review_module,
        "open_workflow",
        lambda checkpoints, **kw: real(checkpoints, audit_db=db.with_name("audit.sqlite"), **kw),
    )
    code = review_module.main(
        [
            "--checkpoints",
            str(db),
            "decide",
            "claim_003",
            "--adjuster",
            "adj-201",
            "--next-step",
            "pay",
            "--payout",
            "10",
            "--reason",
            "x" * 30,
            "--evidence-sha256",
            digest,
        ]
    )
    assert code != 0
    assert "decided" in capsys.readouterr().err


def test_cli_reports_a_lost_race_without_a_traceback(db, monkeypatch, capsys):
    """The decision that loses a race fails inside decide(), after the CLI's own check."""
    import hall_monitor.review as review_module
    import hall_monitor.workflow as workflow_module

    digest = _submit(db, "claim_004")["evidence_sha256"]
    real_open, real_validate = review_module.open_workflow, workflow_module.validate_decision
    nested = []

    def other_adjuster_decides_first(*args, **kwargs):
        if not nested:
            nested.append(True)
            _decide(
                db,
                "claim_004",
                adjuster_id="adj-201",
                next_step=NextStep.DENY,
                payout_usd=0,
                reason="Medication changed 43 days before purchase: pre-existing (5.1).",
            )
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(workflow_module, "validate_decision", other_adjuster_decides_first)
    monkeypatch.setattr(
        review_module,
        "open_workflow",
        lambda checkpoints, **kw: real_open(
            checkpoints, audit_db=db.with_name("audit.sqlite"), **kw
        ),
    )
    code = review_module.main(
        [
            "--checkpoints",
            str(db),
            "decide",
            "claim_004",
            "--adjuster",
            "adj-101",
            "--next-step",
            "deny",
            "--reason",
            "Pre-existing condition under clause 5.1 noted.",
            "--evidence-sha256",
            digest,
        ]
    )
    assert code != 0
    assert "already decided" in capsys.readouterr().err


def test_a_claim_whose_pack_was_logged_but_not_saved_needs_a_person(db, monkeypatch):
    """The crash window HM-05 logs: the pack is in the log but the checkpoint never got it."""
    import hall_monitor.workflow as workflow_module

    real = workflow_module.AuditLog.append

    def crash_after_pack(self, claim_id, event, *args, **kwargs):
        head = real(self, claim_id, event, *args, **kwargs)
        if event == "evidence_pack":
            raise RuntimeError("process died after logging the pack")
        return head

    monkeypatch.setattr(workflow_module.AuditLog, "append", crash_after_pack)
    with pytest.raises(RuntimeError):
        _submit(db, "claim_004")
    monkeypatch.setattr(workflow_module.AuditLog, "append", real)
    with _open(db) as workflow:
        status = workflow.status("claim_004")
    assert status["state"] == "needs_a_person" and "audit log" in status["problem"]


@pytest.mark.parametrize("payout", ["-5", "nan"])
def test_cli_refuses_an_impossible_payout_without_a_traceback(db, monkeypatch, capsys, payout):
    import hall_monitor.review as review_module

    digest = _submit(db, "claim_003")["evidence_sha256"]
    real_open = review_module.open_workflow
    monkeypatch.setattr(
        review_module,
        "open_workflow",
        lambda checkpoints, **kw: real_open(
            checkpoints, audit_db=db.with_name("audit.sqlite"), **kw
        ),
    )
    code = review_module.main(
        [
            "--checkpoints",
            str(db),
            "decide",
            "claim_003",
            "--adjuster",
            "adj-201",
            "--next-step",
            "pay",
            "--payout",
            payout,
            "--reason",
            "x" * 30,
            "--evidence-sha256",
            digest,
        ]
    )
    assert code != 0
    assert "payout" in capsys.readouterr().err


def test_a_decision_is_rejected_while_the_adjusters_file_isnt_approved(db, monkeypatch):
    """Review finding 10: the adjusters file is approved by digest; an unapproved one can't
    authorise a decision, and the claim stays with a person."""
    from hall_monitor import workflow as wf
    from hall_monitor.governance.approval import AdjustersNotApproved

    _submit(db, "claim_004")

    def refuse(*_a, **_k):
        raise AdjustersNotApproved("adjusters not approved: adjusters.yaml is not the approved")

    monkeypatch.setattr(wf, "approved_adjusters", refuse)
    status = _decide(db, "claim_004", **DENY)
    assert status["state"] == "waiting_for_adjuster"
    assert "adjusters not approved" in status["previous_decision_rejected"]


# --- Review finding 7: the same receipts filed again under a new claim ID ---------------------

DUPLICATE_REASON = (
    "duplicate_documents: the same documents were filed with an earlier claim on this policy"
)


def _refiled(claim_id, new_id, change=lambda text: text):
    claim = CLAIMS[claim_id]
    attachments = [a.model_copy(update={"text": change(a.text)}) for a in claim.attachments]
    return claim.model_copy(update={"claim_id": new_id, "attachments": attachments})


def _submit_as(path, truth_id, claim):
    with _open(path, truth_id) as workflow:
        return workflow.submit(claim)


def _triage_entry(workflow, claim_id):
    entries = workflow.audit.entries(claim_id)
    return next(e for e in entries if e["event"] == "triage_result")["payload"]


@pytest.mark.parametrize(
    "change",
    [lambda t: t, lambda t: "  " + t.upper().replace(" ", "\n ") + "\n"],
    ids=["same", "case-and-spacing"],
)
def test_the_same_receipts_under_a_new_claim_id_go_to_an_adjuster(db, change):
    """A small clean claim was auto-paid; filed again under a new ID it must not be paid again
    automatically, whatever the risk agent says."""
    assert _submit(db, "claim_001")["state"] == "decided"
    status = _submit_as(db, "claim_001", _refiled("claim_001", "claim_001b", change))
    assert status["state"] == "waiting_for_adjuster"
    assert DUPLICATE_REASON in status["routing_reasons"]
    with _open(db) as workflow:
        logged, first = _triage_entry(workflow, "claim_001b"), _triage_entry(workflow, "claim_001")
    assert logged["route"] == "adjuster" and DUPLICATE_REASON in logged["routing_reasons"]
    # A keyed hash only: no document text, no policy number.
    assert logged["documents_hmac"] == first["documents_hmac"]
    assert len(logged["documents_hmac"]) == 64
    assert "WTP-" not in str(logged) and "WTP-" not in str(first)


def test_different_receipts_on_the_same_policy_are_not_flagged(db):
    _submit(db, "claim_001")
    other = _refiled("claim_001", "claim_001c", lambda t: t + "\nSecond page.")
    status = _submit_as(db, "claim_001", other)
    assert status["state"] == "decided" and status["final"]["decided_by"] == "auto"


def test_a_claim_is_not_a_duplicate_of_its_own_earlier_run(tmp_path):
    """A restarted triage logs a second result for the same claim ID: not a resubmission."""
    from hall_monitor.governance.audit import AuditLog, TriageRecorder

    log = AuditLog(tmp_path / "audit.sqlite")
    claim = CLAIMS["claim_001"]
    result = triage(
        claim,
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=fake_client(outputs_from_truth(load_ground_truth("claim_001"))),
        on_event=TriageRecorder(log, claim.claim_id),
    )
    recorder = TriageRecorder(log, claim.claim_id)
    recorder("claim_submitted", {"claim": claim, "redactions": {}, "injection_signals": []})
    assert result.routing.route.value == "auto"
    assert not recorder.documents_seen(claim)
    other = TriageRecorder(log, "claim_001b")
    other("claim_submitted", {"claim": claim, "redactions": {}, "injection_signals": []})
    assert other.documents_seen(claim)
    log.close()


# --- Review 38b: the documents hash comes from the submitted claim, not the agent's output -----


def _open_with(path, client):
    return open_workflow(
        path,
        audit_db=path.with_name("audit.sqlite"),
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=client,
    )


def test_a_stopped_claim_with_no_policy_number_still_goes_to_an_adjuster(db):
    """No policy number in the claim and none extracted: triage stops for a person, it doesn't
    crash on hashing the documents."""
    import re

    claim = CLAIMS["claim_001"]
    claim = claim.model_copy(
        update={
            "claim_id": "claim_001_nopolicy",
            "narrative": re.sub(r",? policy WTP-ESS-100231", "", claim.narrative),
        }
    )
    assert "WTP-" not in claim.model_dump_json()
    truth = load_ground_truth("claim_001")
    extraction = truth.extraction.model_copy(update={"policy_number": None})
    client = fake_client(outputs_from_truth(truth) | {type(extraction): extraction})
    with _open_with(db, client) as workflow:
        status = workflow.submit(claim)
        logged = _triage_entry(workflow, claim.claim_id)
    assert status["state"] == "waiting_for_adjuster"
    assert logged["route"] == "adjuster"
    assert len(logged["documents_hmac"]) == 64


def test_refiling_a_claim_whose_first_triage_stopped_is_still_caught(db):
    """The first filing stopped before intake (the agent refused), so no extraction; the same
    documents under a new claim ID are still matched against it."""
    from hall_monitor.models import IntakeExtraction

    truth = load_ground_truth("claim_001")
    refusing = fake_client(outputs_from_truth(truth), stop_reason={IntakeExtraction: "refusal"})
    with _open_with(db, refusing) as workflow:
        first = workflow.submit(CLAIMS["claim_001"])
        logged = _triage_entry(workflow, "claim_001")
    assert first["state"] == "waiting_for_adjuster"
    assert len(logged["documents_hmac"]) == 64
    status = _submit_as(db, "claim_001", _refiled("claim_001", "claim_001b"))
    assert status["state"] == "waiting_for_adjuster"
    assert DUPLICATE_REASON in status["routing_reasons"]


def _raw_checkpoint_bytes(path) -> bytes:
    """Every stored checkpoint and pending write, as the bytes on disk (not through LangGraph)."""
    import sqlite3

    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT checkpoint, metadata FROM checkpoints").fetchall()
        rows += con.execute("SELECT value, value FROM writes").fetchall()
    finally:
        con.close()
    return b"".join(bytes(part) for row in rows for part in row if part is not None)


REJECTED_REASON = "claimant told me by phone her mother was in hospital"
ACCEPTED_REASON = "Denied under the exclusion the coverage step cited; letter to follow."


def _decide_after_a_rejection(db):
    _submit(db, "claim_004")
    rejected = _decide(
        db,
        "claim_004",
        adjuster_id="adj-201",
        next_step=NextStep.PAY,
        payout_usd=99_999.0,
        reason=REJECTED_REASON,
    )
    assert rejected["state"] == "waiting_for_adjuster"
    return _decide(
        db,
        "claim_004",
        adjuster_id="adj-201",
        next_step=NextStep.DENY,
        payout_usd=0,
        reason=ACCEPTED_REASON,
    )


def test_known_limit_a_decided_claims_checkpoint_keeps_its_text_and_reasons(db):
    """HM-10 gap: the checkpoint file keeps, unencrypted and never deleted, the claim as sent and
    every checkpoint version, including the adjuster's name and written reasons, rejected
    decisions too; the audit log keeps reasons only as keyed hashes."""
    status = _decide_after_a_rejection(db)
    assert status["state"] == "decided"
    raw = _raw_checkpoint_bytes(db)
    assert CLAIMS["claim_004"].narrative.encode() in raw
    assert REJECTED_REASON.encode() in raw and ACCEPTED_REASON.encode() in raw
    if status["final"].get("adjuster_name"):
        assert status["final"]["adjuster_name"].encode() in raw


def test_known_limit_erasing_a_claims_pack_leaves_its_checkpoint_copy(db):
    """HM-10 gap: deleting a stored evidence pack (erase_evidence) doesn't reach the checkpoint
    file, which keeps its own copy of the pack and the claim."""
    from hall_monitor.governance.audit import AuditLog

    _submit(db, "claim_004")
    with _open(db) as workflow:
        digest = workflow.status("claim_004")["evidence_sha256"]
    audit = AuditLog(db.with_name("audit.sqlite"))
    try:
        audit.erase_evidence(digest)
        assert audit.get_evidence(digest) is None
    finally:
        audit.close()
    raw = _raw_checkpoint_bytes(db)
    assert digest.encode() in raw
    assert CLAIMS["claim_004"].narrative.encode() in raw


def test_known_limit_a_restarted_triage_calls_every_agent_again(db, monkeypatch):
    """HM-32 gap: the supervisor's whole run is one node of the claims workflow, so nothing is
    saved between its steps; a triage that crashed is restarted from the start with a new case
    budget, and the agents are called, and paid for, again (here, with the scripted supervisor,
    exactly the same calls)."""
    import hall_monitor.workflow as workflow_module

    real = workflow_module.build_evidence_pack

    def crash(*args):
        raise RuntimeError("process died after the agents ran")

    def workflow_with(client):
        return open_workflow(
            db,
            audit_db=db.with_name("audit.sqlite"),
            registry=REGISTRY,
            gateway=GATEWAY,
            wording=WORDING,
            client=client,
        )

    first = fake_client(outputs_from_truth(load_ground_truth("claim_001")))
    monkeypatch.setattr(workflow_module, "build_evidence_pack", crash)
    with pytest.raises(RuntimeError), workflow_with(first) as workflow:
        workflow.submit(CLAIMS["claim_001"])
    monkeypatch.setattr(workflow_module, "build_evidence_pack", real)

    second = fake_client(outputs_from_truth(load_ground_truth("claim_001")))
    with workflow_with(second) as workflow:
        assert workflow.submit(CLAIMS["claim_001"], restart=True)["state"] == "decided"
    assert first.messages.formats() and second.messages.formats() == first.messages.formats()
    assert len(second.messages.supervisor_calls) == len(first.messages.supervisor_calls) > 0
