"""Failure drills (HM-31): many claims at once, failures injected on purpose, and checks in code
that every failure ends with a person, the log verifies, nothing is decided twice and the batch
stays under its cost cap. Offline: the model is scripted from the answer key."""

import json
from pathlib import Path

import pytest
import yaml

from hall_monitor import drills
from hall_monitor.governance.audit import AuditLog
from hall_monitor.kill_switch import STOPS_FILENAME
from hall_monitor.registry import DEFAULT_REGISTRY_DIR

WITH_A_PERSON = {"waiting_for_adjuster", "needs_a_person", "not_started"}


@pytest.fixture(scope="module")
def drill(tmp_path_factory):
    """One drill shared by the tests: every claim once, four workers, a stop two thirds of the
    way through and a cap that leaves the last cases unstarted."""
    out = tmp_path_factory.mktemp("drill")
    report = drills.run(
        out, rounds=1, workers=4, seed=11, fault_share=0.6, stop_after=24, cap_usd=0.2
    )
    return out, report


def _cases(report, **match):
    return [c for c in report["cases"] if all(c[k] == v for k, v in match.items())]


def test_the_plan_is_the_same_for_the_same_seed_and_covers_every_case_fault():
    first = drills.plan(rounds=2, seed=3, fault_share=0.6)
    assert first == drills.plan(rounds=2, seed=3, fault_share=0.6)
    assert first != drills.plan(rounds=2, seed=4, fault_share=0.6)
    assert {c.fault for c in first} == {"none", *drills.CASE_FAULTS}
    assert len({c.case_id for c in first}) == len(first) == 72


def test_the_drill_passes_its_own_checks(drill):
    _, report = drill
    assert report["problems"] == []
    assert report["audit"]["ok"] is True


def test_every_injected_kind_of_failure_happened(drill):
    _, report = drill
    faults = {c["fault"] for c in report["cases"]}
    assert set(drills.CASE_FAULTS) <= faults
    assert report["stop"]["cases_after_stop"] > 0
    assert report["budget"]["not_started"] > 0


def test_every_faulted_or_stopped_case_ends_with_a_person(drill):
    _, report = drill
    for case in report["cases"]:
        if case["fault"] in drills.MODEL_FAULTS | {"crash"} or case["after_stop"]:
            assert case["outcome"] in WITH_A_PERSON, case


def test_cases_untouched_by_any_failure_are_handled_normally(drill):
    """So the drill can't pass by sending everything to a person: a clean case that finished
    before the stop is decided automatically exactly when the answer key routes it so."""
    _, report = drill
    clean = [c for c in _cases(report, fault="none", after_stop=False) if c["before_stop"]]
    assert clean
    for case in clean:
        expected = "decided" if case["key_route"] == "auto" else "waiting_for_adjuster"
        assert case["outcome"] == expected, case


def test_the_stop_reaches_every_case_started_after_it(drill):
    out, report = drill
    log = AuditLog(out / "audit.sqlite", read_only=True)
    try:
        for case in _cases(report, fault="none", after_stop=True):
            if case["outcome"] == "not_started":
                continue
            results = [e for e in log.entries(case["case_id"]) if e["event"] == "triage_result"]
            assert "is stopped" in results[-1]["payload"]["stopped_reason"], case
    finally:
        log.close()


def test_a_crash_after_the_pack_is_logged_leaves_the_case_for_a_person(drill):
    _, report = drill
    crashed = _cases(report, fault="crash")
    assert crashed
    assert all(c["outcome"] == "needs_a_person" for c in crashed if c["started"])


def test_a_duplicate_submission_is_triaged_and_decided_once(drill):
    out, report = drill
    log = AuditLog(out / "audit.sqlite", read_only=True)
    try:
        for case in _cases(report, fault="duplicate", started=True):
            events = [e["event"] for e in log.entries(case["case_id"])]
            assert events.count("evidence_pack") == 1, case
            assert events.count("final_decision") <= 1, case
    finally:
        log.close()


def test_the_batch_stays_under_its_cap_and_unstarted_cases_are_logged(drill):
    out, report = drill
    assert report["budget"]["spent_usd"] <= report["budget"]["cap_usd"]
    log = AuditLog(out / "audit.sqlite", read_only=True)
    try:
        for case in _cases(report, outcome="not_started"):
            events = [e["event"] for e in log.entries(case["case_id"])]
            assert events == ["batch_budget_reached"], case
    finally:
        log.close()


def test_the_cost_in_the_report_is_read_from_the_log(drill):
    out, report = drill
    log = AuditLog(out / "audit.sqlite", read_only=True)
    try:
        logged = sum(
            e["payload"]["cost_usd"] for e in log.entries() if e["event"] == "triage_result"
        )
    finally:
        log.close()
    assert report["budget"]["spent_usd"] == pytest.approx(logged)


SHIPPED_STOPS = (DEFAULT_REGISTRY_DIR / STOPS_FILENAME).read_bytes()


def test_the_drill_never_touches_the_shipped_registry(drill):
    out, report = drill
    assert (DEFAULT_REGISTRY_DIR / STOPS_FILENAME).read_bytes() == SHIPPED_STOPS
    drilled = yaml.safe_load((out / "registry" / STOPS_FILENAME).read_text())
    assert len(drilled["stops"]) == 1
    assert Path(report["registry"]).resolve().is_relative_to(out.resolve())


def test_the_checks_fail_when_a_failure_is_decided_automatically(tmp_path, monkeypatch):
    """Mutation: if the routing rule let every claim through, a wrong answer would be paid
    automatically, and the drill must say so."""
    import hall_monitor.pipeline as pipeline_module
    from hall_monitor.governance.routing import RoutingDecision
    from hall_monitor.models import Route

    monkeypatch.setattr(
        pipeline_module,
        "route_claim",
        lambda *a, **k: RoutingDecision(route=Route.AUTO, reasons=[]),
    )
    report = drills.run(tmp_path, rounds=1, workers=2, seed=11, fault_share=1.0, limit=24)
    assert any("decided automatically" in p for p in report["problems"])


def test_the_checks_fail_when_a_case_is_left_with_no_one(tmp_path, monkeypatch):
    """Mutation: a stopped claim routed automatically is refused at the auto step (coverage is
    missing), so it is left in progress with no one; the drill must say so, not count it."""
    import hall_monitor.pipeline as pipeline_module
    from hall_monitor.governance.routing import RoutingDecision
    from hall_monitor.models import Route

    monkeypatch.setattr(
        pipeline_module,
        "route_stopped",
        lambda reason: RoutingDecision(route=Route.AUTO, reasons=[]),
    )
    report = drills.run(tmp_path, rounds=1, workers=2, seed=11, fault_share=1.0, limit=12)
    assert any("with no one" in p for p in report["problems"])


def test_the_checks_fail_when_the_cap_is_broken(tmp_path, monkeypatch):
    """Mutation: a budget that never refuses must be caught by the cost check, read from the
    log, not trusted from the runner."""
    monkeypatch.setattr(drills.Budget, "reserve", lambda self, n=1: True)
    report = drills.run(tmp_path, rounds=1, workers=2, seed=11, limit=12, cap_usd=0.03)
    assert any("over the cap" in p for p in report["problems"])


def test_the_cli_writes_the_report_and_fails_on_a_problem(tmp_path, capsys, monkeypatch):
    code = drills.main(["--out", str(tmp_path), "--limit", "8", "--workers", "2"])
    assert code == 0
    printed = capsys.readouterr().out
    assert "Failure drill" in printed and "Problems: none" in printed
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["problems"] == []

    monkeypatch.setattr(drills, "check", lambda *a, **k: ["a problem"])
    assert drills.main(["--out", str(tmp_path / "again"), "--limit", "4"]) == 1


def test_the_report_counts_cases_in_flight_at_the_stop(drill):
    """The HM-19 gap (a call in flight finishes) is shown, not hidden: cases begun before the
    stop and finished after it are counted, with how many were decided automatically."""
    _, report = drill
    in_flight = [
        c for c in report["cases"] if c["started"] and not c["after_stop"] and not c["before_stop"]
    ]
    assert report["stop"]["in_flight_at_stop"] == len(in_flight)
    assert report["stop"]["in_flight_decided_automatically"] == sum(
        c["outcome"] == "decided" for c in in_flight
    )


def test_an_unexpected_error_in_one_case_is_reported_not_fatal(tmp_path, monkeypatch):
    """A drill must finish and report: an unforeseen error in one case (a locked database, a
    bug) is recorded by type and the case is flagged as left with no one."""
    real = drills.ClaimWorkflow.submit
    target = drills.plan(rounds=1, seed=11)[0].case_id

    def flaky(self, claim, **kwargs):
        if claim.claim_id == target:
            raise RuntimeError("database is locked")
        return real(self, claim, **kwargs)

    monkeypatch.setattr(drills.ClaimWorkflow, "submit", flaky)
    report = drills.run(tmp_path, rounds=1, workers=2, seed=11, limit=6)
    case = next(c for c in report["cases"] if c["case_id"] == target)
    assert case["errors"] == ["unexpected RuntimeError"]
    assert any(p.startswith(f"{target}:") for p in report["problems"])
