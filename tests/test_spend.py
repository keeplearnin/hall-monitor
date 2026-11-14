"""Cost governance (HM-34, ADR 0030): approved budgets and prices, and the spend ledger's daily
and monthly caps. Offline; no model calls."""

import shutil
import threading
from datetime import UTC, datetime

import pytest
import yaml

from hall_monitor import budgets as budgets_module
from hall_monitor.agentic import Limits
from hall_monitor.budgets import (
    BudgetsNotApproved,
    approved_budgets,
    load_budgets,
)
from hall_monitor.complaints.pipeline import DeskLimits
from hall_monitor.gateway import cost
from hall_monitor.hr.pipeline import LeaveLimits
from hall_monitor.registry import APPROVALS_FILENAME, BUDGETS_FILENAME, DEFAULT_REGISTRY_DIR
from hall_monitor.spend import SpendCapReached, SpendLedger, finish_case, start_case

NOON = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def registry_dir(tmp_path):
    from tests.fakes import TEST_REGISTRY_DIR

    path = tmp_path / "registry"
    shutil.copytree(TEST_REGISTRY_DIR, path)
    return path


def _edit_budgets(registry_dir, change):
    path = registry_dir / BUDGETS_FILENAME
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data))


def _approve(registry_dir):
    path = registry_dir / APPROVALS_FILENAME
    approvals = yaml.safe_load(path.read_text())
    approvals["budgets"] = {"digest": load_budgets(registry_dir / BUDGETS_FILENAME).digest()}
    path.write_text(yaml.safe_dump(approvals))


# --- The budgets file is approved like a hall pass ----------------------------------------------


def test_the_shipped_budgets_are_approved():
    budgets = approved_budgets()
    assert set(budgets.workflows) == {"claims", "complaints", "leave"}


def test_an_unreviewed_change_to_the_budgets_is_refused(registry_dir):
    _edit_budgets(registry_dir, lambda d: d["workflows"]["claims"].update(daily_usd=6.0))
    with pytest.raises(BudgetsNotApproved, match="not the approved version"):
        approved_budgets(registry_dir)
    _approve(registry_dir)  # the reviewed approval
    assert approved_budgets(registry_dir).workflows["claims"].daily_usd == 6.0


def test_a_comment_or_layout_change_is_not_a_change(registry_dir):
    path = registry_dir / BUDGETS_FILENAME
    path.write_text("# reworded\n" + path.read_text().replace("{input: 0.10", "{input: 0.1"))
    approved_budgets(registry_dir)


def test_a_price_change_needs_approval_too(registry_dir):
    _edit_budgets(
        registry_dir, lambda d: d["prices"]["per_mtok_usd"]["claude-haiku-5-5"].update(input=0.0)
    )
    with pytest.raises(BudgetsNotApproved):
        approved_budgets(registry_dir)


def test_a_missing_or_broken_budgets_file_is_refused(registry_dir):
    (registry_dir / BUDGETS_FILENAME).write_text("workflows: [")
    with pytest.raises(BudgetsNotApproved, match="can't be read"):
        approved_budgets(registry_dir)
    (registry_dir / BUDGETS_FILENAME).unlink()
    with pytest.raises(BudgetsNotApproved, match="can't be read"):
        approved_budgets(registry_dir)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda d: d["workflows"]["claims"].update(owner="nobody"), "not a person"),
        (lambda d: d["workflows"].pop("leave"), "no budget for leave"),
        (lambda d: d["workflows"]["claims"].update(daily_usd=60.0), "daily cap is above"),
        (lambda d: d["workflows"]["leave"].update(daily_usd=0.01), "case budget is above"),
        (lambda d: d["workflows"]["claims"]["case"].update(max_cost_usd=-1), "greater than 0"),
    ],
)
def test_budgets_that_don_t_make_sense_are_refused(change, problem):
    data = yaml.safe_load((DEFAULT_REGISTRY_DIR / BUDGETS_FILENAME).read_text())
    change(data)
    with pytest.raises(ValueError, match=problem):
        budgets_module.Budgets.model_validate(data).check(
            frozenset({"own-301", "own-302", "own-401", "own-501"})
        )


def test_prices_come_from_the_approved_file():
    budgets = approved_budgets()
    assert cost.PRICES_PER_MTOK == {
        m: (p.input, p.output) for m, p in budgets.prices.per_mtok_usd.items()
    }
    assert cost.CACHE_WRITE_MULTIPLIER == budgets.prices.cache_write_multiplier
    assert cost.CACHE_READ_MULTIPLIER == budgets.prices.cache_read_multiplier


def test_with_unapproved_budgets_no_model_has_a_price(monkeypatch):
    def refuse(*args, **kwargs):
        raise BudgetsNotApproved("spend budgets not approved: budgets.yaml is not the approved")

    monkeypatch.setattr(cost, "approved_budgets", refuse)
    table, _, _ = cost._approved_prices()
    assert table == {}


def test_the_limits_defaults_match_the_approved_case_budgets():
    """Tests build partial limits from the dataclass defaults; the pipelines use the approved
    file. The two must not drift apart."""
    workflows = approved_budgets().workflows
    for limits, name in (
        (Limits(), "claims"),
        (DeskLimits(), "complaints"),
        (LeaveLimits(), "leave"),
    ):
        case = workflows[name].case
        assert (limits.max_steps, limits.max_cost_usd, limits.max_seconds) == (
            case.max_steps,
            case.max_cost_usd,
            case.max_seconds,
        ), name


# --- The ledger -------------------------------------------------------------------------------


def _ledger(tmp_path, when=NOON):
    return SpendLedger(tmp_path / "spend.sqlite", clock=lambda: when)


def test_a_case_reserves_its_budget_and_settles_at_what_it_cost(tmp_path):
    ledger = _ledger(tmp_path)
    start = start_case("claims", "claim_001", ledger)
    assert start.reservation.reserved_usd == 0.25
    assert ledger.committed("claims", day="2026-11-13") == 0.25
    finish_case(ledger, start, 0.04)
    assert ledger.committed("claims", day="2026-11-13") == 0.04
    assert ledger.committed("claims", month="2026-11") == 0.04


def test_the_daily_cap_refuses_a_case_that_would_pass_it(tmp_path):
    ledger = _ledger(tmp_path)
    starts = [start_case("claims", f"c{i}", ledger) for i in range(20)]  # 20 x 0.25 = 5.00
    with pytest.raises(SpendCapReached, match=r"^spend cap reached: daily claims cap of 5.00 USD$"):
        start_case("claims", "c20", ledger)
    finish_case(ledger, starts[0], 0.05)  # settling frees the rest of a reservation:
    finish_case(ledger, starts[1], 0.05)  # 4.60 committed, room for one more case
    start_case("claims", "c21", ledger)


def test_the_monthly_cap_counts_every_day_of_the_month(tmp_path):
    for day in range(1, 11):  # 10 days x 20 cases x 0.25 = 50.00, the monthly cap
        ledger = _ledger(tmp_path, NOON.replace(day=day))
        for i in range(20):
            start_case("claims", f"d{day}-{i}", ledger)
    ledger = _ledger(tmp_path, NOON.replace(day=11))
    with pytest.raises(SpendCapReached, match="monthly claims cap of 50.00 USD"):
        start_case("claims", "late", ledger)
    _ledger(tmp_path, NOON.replace(month=12, day=1)).reserve(approved_budgets(), "claims", "next")


def test_caps_are_per_workflow(tmp_path):
    ledger = _ledger(tmp_path)
    for i in range(50):  # 50 x 0.02 = 1.00, the desk's daily cap
        start_case("complaints", f"k{i}", ledger)
    with pytest.raises(SpendCapReached, match="complaints"):
        start_case("complaints", "k50", ledger)
    start_case("leave", "l0", ledger)
    start_case("claims", "c0", ledger)


def test_an_unpriced_cost_and_a_crash_stay_reserved(tmp_path):
    ledger = _ledger(tmp_path)
    unpriced = start_case("claims", "c1", ledger)
    start_case("claims", "c2", ledger)  # never settled: the process died
    finish_case(ledger, unpriced, None)
    assert ledger.committed("claims", day="2026-11-13") == 0.5


def test_a_reservation_settles_once(tmp_path):
    ledger = _ledger(tmp_path)
    start = start_case("claims", "c1", ledger)
    finish_case(ledger, start, 0.04)
    finish_case(ledger, start, 0.0)
    assert ledger.committed("claims", day="2026-11-13") == 0.04


def test_without_a_ledger_a_case_still_needs_approved_budgets(registry_dir):
    assert start_case("claims", "c1", None, registry_dir).reservation is None
    _edit_budgets(registry_dir, lambda d: d["workflows"]["claims"].update(daily_usd=9.0))
    with pytest.raises(BudgetsNotApproved):
        start_case("claims", "c1", None, registry_dir)


def test_two_processes_can_t_both_take_the_last_of_a_cap(tmp_path):
    """Reservations are taken inside BEGIN IMMEDIATE on a shared file: of many workers racing
    for a cap with room for exactly 20 cases, exactly 20 get one."""
    path = tmp_path / "spend.sqlite"
    SpendLedger(path)
    taken, refused = [], []
    barrier = threading.Barrier(40)

    def worker(i):
        ledger = SpendLedger(path, clock=lambda: NOON)  # its own connection, as a process would
        barrier.wait()
        try:
            start_case("claims", f"c{i}", ledger)
            taken.append(i)
        except SpendCapReached:
            refused.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(taken) == 20 and len(refused) == 20
    assert SpendLedger(path).committed("claims", day="2026-11-13") == 5.0


def test_the_ledger_holds_no_case_content(tmp_path):
    import sqlite3

    ledger = _ledger(tmp_path)
    finish_case(ledger, start_case("leave", "msg_001", ledger), 0.003)
    columns = [r[1] for r in sqlite3.connect(ledger.path).execute("PRAGMA table_info(spend)")]
    assert columns == [
        "id",
        "workflow",
        "case_id",
        "day",
        "month",
        "reserved_usd",
        "actual_usd",
        "opened_at",
        "settled_at",
    ]


# --- The caps in the three workflows: refused cases go to a person, with no model call ---------


def _full(tmp_path, workflow, n):
    ledger = _ledger(tmp_path)
    for i in range(n):
        start_case(workflow, f"filler-{i}", ledger)
    return ledger


@pytest.fixture
def unapproved(monkeypatch):
    """The budgets file edited without approval."""
    import hall_monitor.spend as spend

    def refuse(*args, **kwargs):
        raise BudgetsNotApproved(
            "spend budgets not approved: budgets.yaml is not the approved version"
        )

    monkeypatch.setattr(spend, "approved_budgets", refuse)


@pytest.mark.parametrize("agentic", [True, False])
def test_a_claim_over_the_cap_goes_to_a_person_with_no_model_call(tmp_path, agentic):
    from hall_monitor.models import Route
    from tests.test_agentic import _run as run_claim

    ledger = _full(tmp_path, "claims", 20)
    result, client = run_claim(spend=ledger, agentic=agentic)
    assert result.routing.route is Route.ADJUSTER
    assert result.stopped_reason == "spend cap reached: daily claims cap of 5.00 USD"
    assert client.messages.calls == [] and client.messages.supervisor_calls == []
    assert result.cost_usd == 0.0


@pytest.mark.parametrize("agentic", [True, False])
def test_a_claim_under_the_cap_settles_at_its_cost(tmp_path, agentic):
    from hall_monitor.models import Route
    from tests.test_agentic import _run as run_claim

    ledger = _ledger(tmp_path)
    result, _ = run_claim(spend=ledger, agentic=agentic)
    assert result.routing.route is Route.AUTO
    assert result.cost_usd > 0
    assert ledger.committed("claims", day="2026-11-13") == pytest.approx(result.cost_usd)


def test_a_claim_that_fails_part_way_still_settles(tmp_path):
    from tests.test_agentic import A, R, _decide
    from tests.test_agentic import _run as run_claim

    ledger = _ledger(tmp_path)
    result, _ = run_claim(spend=ledger, supervisor=_decide(A.RUN_CRITIC, R.NEEDS_REVIEW))
    assert result.stopped_reason  # three refused steps
    assert ledger.committed("claims", day="2026-11-13") == pytest.approx(result.cost_usd)


def test_a_claim_with_unapproved_budgets_goes_to_a_person(unapproved):
    from hall_monitor.models import Route
    from tests.test_agentic import _run as run_claim

    result, client = run_claim()
    assert result.routing.route is Route.ADJUSTER
    assert result.stopped_reason == (
        "spend budgets not approved: budgets.yaml is not the approved version"
    )
    assert client.messages.calls == [] and client.messages.supervisor_calls == []


def test_a_claim_s_case_budget_comes_from_the_approved_file(monkeypatch):
    """The pipelines read the case budget from budgets.yaml, not the dataclass defaults."""
    import hall_monitor.spend as spend
    from tests.test_agentic import _run as run_claim

    real = approved_budgets()
    tight = real.model_copy(deep=True)
    tight.workflows["claims"].case.max_steps = 3
    monkeypatch.setattr(spend, "approved_budgets", lambda *a, **k: tight)
    result, _ = run_claim()
    assert result.stopped_reason == "budget exhausted: 3 supervisor steps"


@pytest.mark.parametrize("agentic", [True, False])
def test_a_complaint_over_the_cap_goes_to_specialist_review_with_no_model_call(tmp_path, agentic):
    from tests.test_desk_agentic import _run as run_desk

    ledger = _full(tmp_path, "complaints", 50)
    result, client = run_desk(spend=ledger, agentic=agentic)
    assert result.route.queue == "specialist_review"
    assert "spend_refused" in result.route.reasons
    assert result.stopped_reason == "spend cap reached: daily complaints cap of 1.00 USD"
    assert client.messages.calls == [] and client.messages.supervisor_calls == []
    assert result.triage is None and result.cost_usd == 0.0
    if agentic:  # no agent read it: high urgency on the agentic path (ADR 0028)
        assert result.route.urgency.value == "high"


def test_a_complaint_under_the_cap_settles_at_its_cost(tmp_path):
    from tests.test_desk_agentic import _run as run_desk

    ledger = _ledger(tmp_path)
    result, _ = run_desk(spend=ledger)
    assert "spend_refused" not in result.route.reasons
    assert ledger.committed("complaints", day="2026-11-13") == pytest.approx(result.cost_usd)


def test_a_complaint_with_unapproved_budgets_goes_to_specialist_review(unapproved):
    from tests.test_desk_agentic import _run as run_desk

    result, client = run_desk()
    assert result.route.queue == "specialist_review" and "spend_refused" in result.route.reasons
    assert client.messages.calls == [] and client.messages.supervisor_calls == []


@pytest.mark.parametrize("agentic", [True, False])
def test_a_leave_request_over_the_cap_goes_to_an_hr_director_with_no_model_call(tmp_path, agentic):
    from hall_monitor.governance.audit import AuditLog
    from tests.test_hr_agentic import _run as run_leave

    ledger = _full(tmp_path, "leave", 50)
    audit = AuditLog(tmp_path / "audit.sqlite")
    result, client = run_leave(spend=ledger, agentic=agentic, audit=audit)
    assert result.route.route == "hr_review" and result.route.decided_by == "hr_director"
    assert "spend_refused" in result.route.reasons
    assert result.stopped_reason == "spend cap reached: daily leave cap of 1.00 USD"
    assert client.messages.calls == [] and client.messages.supervisor_calls == []
    # HR can still sign it: the evidence pack is stored.
    assert audit.has(result.request_id, "leave_evidence")


def test_a_leave_request_under_the_cap_settles_at_its_cost(tmp_path):
    from tests.test_hr_agentic import _run as run_leave

    ledger = _ledger(tmp_path)
    result, _ = run_leave(spend=ledger)
    assert "spend_refused" not in result.route.reasons
    assert ledger.committed("leave", day="2026-11-13") == pytest.approx(result.cost_usd)


def test_a_leave_request_with_unapproved_budgets_goes_to_an_hr_director(unapproved):
    from tests.test_hr_agentic import _run as run_leave

    result, client = run_leave()
    assert result.route.decided_by == "hr_director" and "spend_refused" in result.route.reasons
    assert client.messages.calls == [] and client.messages.supervisor_calls == []


# --- The panel shows the refusals in the code's own words ------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "spend cap reached: daily claims cap of 5.00 USD",
        "spend cap reached: monthly claims cap of 50.00 USD",
        "spend budgets not approved: budgets.yaml is not the approved version",
        "spend budgets not approved: budgets.yaml can't be read (FileNotFoundError)",
    ],
)
def test_the_panel_shows_a_claim_s_spend_refusal(reason):
    from hall_monitor.panel import views

    assert views.shown_stop(reason) == reason
    assert views.shown_reason(f"pipeline stopped: {reason}") == f"pipeline stopped: {reason}"
    assert views.shown_stop(reason.replace("claims", "leave")) in (views.WITHHELD, reason)


@pytest.mark.parametrize(
    "reason",
    [
        "spend cap reached: daily leave cap of 1.00 USD",
        "spend budgets not approved: budgets.yaml is not the approved version",
    ],
)
def test_the_panel_shows_a_leave_request_s_spend_refusal(reason):
    from hall_monitor.panel import views

    assert views._template(reason, views._HR_STOP_RE) == reason


# --- Every live entry point holds its cases to the caps -----------------------------------------


def test_the_claims_cli_passes_the_ledger(tmp_path, monkeypatch):
    import contextlib

    import hall_monitor.run as claims_run
    from hall_monitor.spend import SpendLedger as Ledger

    seen = []
    monkeypatch.setattr(claims_run, "triage", lambda *a, **k: seen.append(k["spend"]) or _Dump())

    @contextlib.contextmanager
    def workflow(*args, **kwargs):
        seen.append(kwargs["spend"])
        yield _Workflow()

    monkeypatch.setattr(claims_run, "open_workflow", workflow)
    ledger = tmp_path / "spend.sqlite"
    claim = "data/synthetic/claims/claim_001.json"
    claims_run.main(["--claim", claim, "--triage-only", "--spend-db", str(ledger)])
    claims_run.main(["--claim", claim, "--spend-db", str(ledger)])
    assert len(seen) == 2 and all(isinstance(s, Ledger) and s.path == ledger for s in seen)


class _Dump:
    def model_dump_json(self, **kwargs):
        return "{}"


class _Workflow:
    def submit(self, claim, restart=False):
        return {"state": "decided"}


def test_the_desk_cli_passes_the_ledger_live_and_not_in_demo(tmp_path, monkeypatch):
    import hall_monitor.complaints.run as desk_run
    from hall_monitor.spend import SpendLedger as Ledger

    seen = []
    monkeypatch.setattr(desk_run, "DEFAULT_SPEND_DB", tmp_path / "spend.sqlite")
    monkeypatch.setattr(desk_run, "model_client", lambda **k: object())
    monkeypatch.setattr(desk_run, "run", lambda *a: seen.append(a[4]) or {})
    audit = tmp_path / "audit.sqlite"
    desk_run.main(["--live", "--limit", "1", "--audit-db", str(audit)])
    desk_run.main(["--demo", "--limit", "1", "--audit-db", str(tmp_path / "demo.sqlite")])
    assert isinstance(seen[0], Ledger) and seen[1] is None


def test_the_hr_cli_passes_the_ledger(tmp_path, monkeypatch):
    import hall_monitor.hr.run as hr_run
    from hall_monitor.spend import SpendLedger as Ledger

    seen = []
    monkeypatch.setattr(hr_run, "DEFAULT_SPEND_DB", tmp_path / "spend.sqlite")
    monkeypatch.setattr(hr_run, "run", lambda *a, **k: seen.append(k["spend"]) or [])
    hr_run.main(["lr_001", "--audit-db", str(tmp_path / "audit.sqlite")])
    assert isinstance(seen[0], Ledger)


@pytest.mark.parametrize("module", ["hall_monitor.complaints.submit", "hall_monitor.hr.submit"])
def test_the_live_forms_pass_the_ledger(tmp_path, monkeypatch, module):
    import importlib
    import sys
    import types

    from hall_monitor.spend import SpendLedger as Ledger

    form = importlib.import_module(module)
    seen = []
    monkeypatch.setattr(form, "DEFAULT_SPEND_DB", tmp_path / "spend.sqlite")
    monkeypatch.setattr(form, "create_app", lambda **k: seen.append(k.get("spend")))
    if hasattr(form, "default_client"):
        monkeypatch.setattr(form, "default_client", lambda: object())
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **k: None))
    form.main(["--live", "--audit-db", str(tmp_path / "audit.sqlite")])
    assert isinstance(seen[0], Ledger)


def _submit_live(tmp_path, module, ledger):
    """One live submission through the form, with a fake model; the fake client after."""
    from starlette.testclient import TestClient

    from tests.fakes import fake_client

    if module == "complaints":
        from hall_monitor.complaints import submit as form
        from tests.test_complaint_triage import answer

        client = fake_client(
            {
                __import__(
                    "hall_monitor.complaints.triage_agent", fromlist=["x"]
                ).ComplaintTriage: answer()
            }
        )
    else:
        from hall_monitor.hr import submit as form
        from hall_monitor.hr.models import LeaveExplanation, LeaveIntake
        from tests.test_hr_agents import _explanation, _intake_for

        client = fake_client({LeaveIntake: _intake_for, LeaveExplanation: _explanation})
    from tests.fakes import TEST_REGISTRY_DIR

    app = form.create_app(
        live=True,
        audit_db=tmp_path / "audit.sqlite",
        registry_dir=TEST_REGISTRY_DIR,
        client=client,
        spend=ledger,
    )
    http = TestClient(app, base_url="http://127.0.0.1")
    token = http.get("/").text.split('name="token" value="')[1].split('"')[0]
    if module == "complaints":
        from tests.test_complaint_submit import PLAIN

        data = PLAIN
    else:
        from hall_monitor.hr.data import load_employees

        data = {
            "employee_id": next(iter(load_employees())),
            "text": "I need leave from 2026-12-01 to 2026-12-14 to care for my father.",
        }
    response = http.post(
        "/submit", data={"token": token, **data}, headers={"origin": "http://127.0.0.1"}
    )
    return client, response


@pytest.mark.parametrize(("module", "workflow"), [("complaints", "complaints"), ("hr", "leave")])
def test_a_live_form_s_submission_is_held_to_the_cap(tmp_path, module, workflow):
    """The form's own pipeline call carries the ledger: under the cap a live submission reaches
    the model; with the cap used up, it goes to a person with no model call."""
    ledger = _ledger(tmp_path / "open")
    client, response = _submit_live(tmp_path / "open", module, ledger)
    assert response.status_code < 400, response.text[:300]
    assert client.messages.calls  # the control: the submission really ran
    assert ledger.committed(workflow, day="2026-11-13") > 0

    full = _full(tmp_path / "full", workflow, 50)
    client, response = _submit_live(tmp_path / "full", module, full)
    assert response.status_code < 400
    assert client.messages.calls == [] and client.messages.supervisor_calls == []


# --- Review 28: a broken ledger, the workflow path, prices read live, interruptions ---------


class _BrokenLedger:
    """A ledger that can't be reached: locked past its timeout, or a disk error."""

    path = None

    def reserve(self, *args, **kwargs):
        import sqlite3

        raise sqlite3.OperationalError("database is locked")

    def settle(self, *args, **kwargs):
        raise AssertionError("nothing was reserved")


@pytest.mark.parametrize("agentic", [True, False])
def test_a_broken_ledger_sends_a_claim_to_a_person_with_no_model_call(agentic):
    from hall_monitor.models import Route
    from tests.test_agentic import _run as run_claim

    result, client = run_claim(spend=_BrokenLedger(), agentic=agentic)
    assert result.routing.route is Route.ADJUSTER
    assert result.stopped_reason == "spend ledger unavailable (OperationalError)"
    assert client.messages.calls == [] and client.messages.supervisor_calls == []


@pytest.mark.parametrize("agentic", [True, False])
def test_a_broken_ledger_sends_a_complaint_to_specialist_review_blaming_no_agent(agentic):
    from tests.test_desk_agentic import _run as run_desk

    result, client = run_desk(spend=_BrokenLedger(), agentic=agentic)
    assert result.route.queue == "specialist_review"
    assert "spend_refused" in result.route.reasons
    assert not {"agent_failed", "supervisor_failed"} & set(result.route.reasons)
    assert result.stopped_reason == "spend ledger unavailable (OperationalError)"
    assert client.messages.calls == [] and client.messages.supervisor_calls == []


@pytest.mark.parametrize("agentic", [True, False])
def test_a_broken_ledger_sends_a_leave_request_to_an_hr_director(tmp_path, agentic):
    from hall_monitor.governance.audit import AuditLog
    from tests.test_hr_agentic import _run as run_leave

    audit = AuditLog(tmp_path / "audit.sqlite")
    result, client = run_leave(spend=_BrokenLedger(), agentic=agentic, audit=audit)
    assert result.route.decided_by == "hr_director" and "spend_refused" in result.route.reasons
    assert result.stopped_reason == "spend ledger unavailable (OperationalError)"
    assert client.messages.calls == [] and client.messages.supervisor_calls == []
    assert audit.has(result.request_id, "leave_evidence")


def test_a_ledger_that_fails_while_settling_doesn_t_lose_the_case(tmp_path, monkeypatch):
    """The case has already been routed; its reservation just stays counted in full."""
    import sqlite3

    from hall_monitor.models import Route
    from tests.test_agentic import _run as run_claim

    ledger = _ledger(tmp_path)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ledger, "settle", broken)
    result, _ = run_claim(spend=ledger)
    assert result.routing.route is Route.AUTO
    assert ledger.committed("claims", day="2026-11-13") == 0.25


def test_the_claims_workflow_holds_a_claim_to_the_cap(tmp_path):
    """The live claims path end to end: hall_monitor.run opens the workflow with the ledger,
    and the workflow's triage step passes it on."""
    from hall_monitor.data import load_ground_truth
    from hall_monitor.workflow import open_workflow
    from tests.fakes import fake_client, outputs_from_truth
    from tests.test_workflow import CLAIMS, GATEWAY, REGISTRY, WORDING

    client = fake_client(outputs_from_truth(load_ground_truth("claim_001")))
    with open_workflow(
        tmp_path / "checkpoints.sqlite",
        audit_db=tmp_path / "audit.sqlite",
        registry=REGISTRY,
        gateway=GATEWAY,
        wording=WORDING,
        client=client,
        spend=_full(tmp_path, "claims", 20),
    ) as workflow:
        status = workflow.submit(CLAIMS["claim_001"])
    assert status["state"] == "waiting_for_adjuster"
    assert client.messages.calls == [] and client.messages.supervisor_calls == []


@pytest.fixture
def live_prices(registry_dir, monkeypatch):
    """call_cost reading prices from a registry copy, as it reads the shipped one."""
    monkeypatch.setattr(cost, "_REGISTRY_DIR", registry_dir)
    monkeypatch.setattr(cost, "_SOURCE", None)
    monkeypatch.setattr(cost, "PRICES_PER_MTOK", {})
    return registry_dir


def _call(model="claude-haiku-5-5"):
    from hall_monitor.models import AgentCall

    return AgentCall(
        agent_id="intake",
        agent_version="0",
        model=model,
        manifest_digest="x",
        prompt_version="x",
        prompt_template_sha256="x",
        system_prompt_sha256="x",
        input_sha256="x",
        request_ids=[],
        stop_reason="end_turn",
        api_calls=1,
        model_tier="standard",
        input_tokens=1_000_000,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=0,
        seconds=0.0,
    )


def test_call_cost_uses_the_approved_prices_and_picks_up_an_approved_change(live_prices):
    assert cost.call_cost(_call()) == pytest.approx(0.10)
    _edit_budgets(
        live_prices, lambda d: d["prices"]["per_mtok_usd"]["claude-haiku-5-5"].update(input=0.2)
    )
    _approve(live_prices)
    assert cost.call_cost(_call()) == pytest.approx(0.20)  # no restart


def test_call_cost_prices_nothing_while_the_budgets_are_unapproved(live_prices):
    from hall_monitor.gateway.cost import UnpricedModelError

    assert cost.call_cost(_call()) == pytest.approx(0.10)
    _edit_budgets(
        live_prices, lambda d: d["prices"]["per_mtok_usd"]["claude-haiku-5-5"].update(input=0.0)
    )
    with pytest.raises(UnpricedModelError):
        cost.call_cost(_call())


def test_a_desk_case_budget_comes_from_the_approved_file(monkeypatch):
    import hall_monitor.spend as spend
    from tests.test_desk_agentic import _run as run_desk

    tight = approved_budgets().model_copy(deep=True)
    tight.workflows["complaints"].case.max_steps = 1
    monkeypatch.setattr(spend, "approved_budgets", lambda *a, **k: tight)
    result, _ = run_desk()
    assert "budget exhausted: 1 supervisor steps" in result.stopped_reason


def test_a_leave_case_budget_comes_from_the_approved_file(monkeypatch):
    import hall_monitor.spend as spend
    from tests.test_hr_agentic import _run as run_leave

    tight = approved_budgets().model_copy(deep=True)
    tight.workflows["leave"].case.max_steps = 1
    monkeypatch.setattr(spend, "approved_budgets", lambda *a, **k: tight)
    result, _ = run_leave()
    assert "budget exhausted: 1 supervisor steps" in result.stopped_reason


def test_an_unpriced_claim_stays_reserved_in_full(tmp_path, monkeypatch):
    from tests.test_agentic import _run as run_claim

    monkeypatch.delitem(cost.PRICES_PER_MTOK, "claude-haiku-5-5")
    ledger = _ledger(tmp_path)
    result, _ = run_claim(spend=ledger)
    assert "cost unknown" in result.stopped_reason
    assert ledger.committed("claims", day="2026-11-13") == 0.25


def test_an_interrupted_claim_stays_reserved_in_full(tmp_path):
    """A KeyboardInterrupt or SystemExit mid-claim: the request in flight may have been billed,
    so the reservation keeps counting in full, as a crash's does."""
    from hall_monitor.data import load_ground_truth
    from hall_monitor.models import CoverageFinding
    from tests.fakes import outputs_from_truth
    from tests.test_agentic import _run as run_claim

    def interrupt(request):
        raise KeyboardInterrupt

    outputs = outputs_from_truth(load_ground_truth("claim_001"))
    outputs[CoverageFinding] = interrupt
    ledger = _ledger(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        run_claim(spend=ledger, outputs=outputs)
    assert ledger.committed("claims", day="2026-11-13") == 0.25
