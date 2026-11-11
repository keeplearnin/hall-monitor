"""HM-28: complaint desk evals, fairness and the panel page. Offline: the scripted stand-in only."""

import json

import pytest
from starlette.testclient import TestClient

from hall_monitor import no_send
from hall_monitor.complaints import evals, routing
from hall_monitor.complaints import run as complaint_run
from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
from hall_monitor.complaints.scripted import scripted_client
from hall_monitor.egress import audit_refusals
from hall_monitor.evals import gate
from hall_monitor.governance.audit import AuditLog
from hall_monitor.panel import desk, views
from hall_monitor.panel.app import create_app
from hall_monitor.registry import Registry, load_approvals
from tests.fakes import TEST_REGISTRY_DIR

REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
PINNED = load_pinned(PINNED_NAME)
PUBLISHED = json.loads((evals.SCRIPTED_DIR / "results.json").read_text())


@pytest.fixture(scope="module")
def fresh():
    return evals.evaluate(PINNED, scripted_client(), REGISTRY, "scripted")


def test_the_published_scripted_result_is_what_the_code_gives_now(fresh):
    assert fresh["summary"] == PUBLISHED["summary"]
    assert PUBLISHED["meta"]["mode"] == "scripted"
    assert PUBLISHED["meta"]["snapshot_manifest_sha256"] == evals.PINNED_MANIFESTS[PINNED_NAME]


def test_the_published_result_meets_the_gate():
    ok, results = gate.run_gate(approvals=load_approvals(TEST_REGISTRY_DIR / "approvals.yaml"))
    [desk_check] = [r for r in results if r.name.startswith("Complaint desk, scripted")]
    assert desk_check.state == "pass", desk_check.failures + desk_check.notes


def test_the_eval_scores_routing_against_the_answer_key_not_the_patterns(monkeypatch):
    """Weaken a pattern: the eval must see it, because must-route comes from the key."""
    patterns = dict(routing.CONCERN_PATTERNS)
    patterns.pop("identity_theft")
    monkeypatch.setattr(routing, "CONCERN_PATTERNS", patterns)
    summary = evals.evaluate(PINNED, scripted_client(), REGISTRY, "scripted")["summary"]
    assert summary["must_route_reason_missing"] > 0 and summary["must_route_missed"] > 0


def test_a_gate_fails_a_result_that_missed_a_must_route_complaint(tmp_path):
    data = json.loads(json.dumps(PUBLISHED))
    data["summary"]["must_route_missed"] = 1
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data))
    spec = {
        "name": "probe",
        "results": str(path),
        "required": True,
        "max": {"must_route_missed": 0},
    }
    result = gate.check(spec, load_approvals(TEST_REGISTRY_DIR / "approvals.yaml"), "warn")
    assert result.state == "fail"


def test_the_counterfactual_check_catches_location_reaching_the_agent(monkeypatch):
    real = evals.render_complaint

    def leaky(c, narrative, public):
        return real(c, narrative, public) + f"<state>{c.state}</state>"

    monkeypatch.setattr(evals, "render_complaint", leaky)
    assert evals.flips(PINNED[0])
    monkeypatch.undo()
    assert not any(evals.flips(c) for c in PINNED)


def test_group_rates_are_reported_by_zip_region_and_tag(fresh):
    fairness = fresh["fairness"]
    assert set(fairness["by_tag"]) >= {"older_american", "servicemember", "none"}
    assert all(g["n"] > 0 for g in fairness["by_zip_region"].values())
    assert fresh["summary"]["tag_specialist_rate_older_american"] == 1.0


def test_the_label_key_covers_only_routing_reasons():
    key = evals.load_label_key()
    assert set(key.values()) <= set(routing.REASONS)
    assert "Debt is not yours" in key and "Transaction was not authorized" in key


def test_a_live_eval_needs_a_limit():
    with pytest.raises(SystemExit):
        evals.main(["--live"])


# The panel page.


@pytest.fixture
def desk_src(tmp_path):
    db = tmp_path / "audit.sqlite"
    audit = AuditLog(db)
    complaint_run.run(PINNED[:30], audit, scripted_client(), REGISTRY)
    audit.append(
        "pull-20261111T000000Z-abcdef",
        "complaint_pull",
        "pipeline",
        {
            "pull_id": "pull-20261111T000000Z-abcdef",
            "status": "failed",
            "error": "CfpbError",
            "started_at": "2026-11-11T00:00:00+00:00",
            "seconds": 1.5,
            "fetched": 100,
            "new": 90,
            "updated": 5,
            "unchanged": 5,
            "quarantined": 0,
            "requests": 3,
            "retries": 2,
        },
    )
    audit_refusals(audit)(
        {
            "policy": "cfpb_complaint_api",
            "method": "POST",
            "host": "www.consumerfinance.gov",
            "url_sha256": "0" * 64,
            "reason": "method_host_or_path_not_allowed",
        }
    )
    audit.close()
    return views.Sources(registry_dir=TEST_REGISTRY_DIR, audit_db=db)


def test_the_desk_page_shows_the_queue_reasons_pulls_and_the_no_send_status(desk_src):
    data = desk.complaint_desk(desk_src)
    assert len(data["complaints"]) == 30
    assert data["no_send"] == {
        "static_check_passes": True,
        "static_check_findings": 0,
        "egress_refusals": 1,
        "drafts_stored": 30,
        "drafts_not_marked_never_sent": 0,
    }
    assert data["pulls"][0]["status"] == "failed" and data["pulls"][0]["retries"] == 2
    page = TestClient(create_app(desk_src), base_url="http://localhost").get("/complaints.html")
    assert page.status_code == 200
    text = page.text
    assert "No reply, no send (HM-25)" in text and "holding" in text
    assert "specialist review" in text and "Pulls from CFPB" in text
    # Never the summaries or drafts.
    assert "The consumer complains about" not in text and "Thank you for your complaint" not in text


def test_complaints_never_show_up_as_claims(desk_src):
    assert views.cases(desk_src) == []
    api = TestClient(create_app(desk_src), base_url="http://localhost").get("/api/complaints.json")
    assert api.json()["queues"]["specialist_review"] >= 1


def test_the_page_says_when_the_static_check_fails(desk_src, monkeypatch):
    monkeypatch.setattr(
        no_send, "scan", lambda root=None: [no_send.Finding("x.py", 1, "banned_import", "smtplib")]
    )
    text = desk.complaints_page(desk.complaint_desk(desk_src), demo=False)
    assert "check failed" in text and "1 findings" in text


# Review 17.


def test_a_run_where_every_model_call_fails_fails_the_gate(tmp_path):
    from hall_monitor.complaints.triage_agent import ComplaintTriage
    from tests.fakes import fake_client

    def down(kwargs):
        raise ValueError("outage")

    data = evals.evaluate(PINNED[:20], fake_client({ComplaintTriage: down}), REGISTRY, "live")
    assert data["summary"]["agent_failed"] == 20
    data["meta"] = PUBLISHED["meta"] | {"mode": "live"}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data))
    config = __import__("yaml").safe_load(gate.CONFIG.read_text())
    [spec] = [s for s in config["checks"] if s["name"] == "Complaint desk, live model"]
    result = gate.check(
        spec | {"results": str(path)}, load_approvals(TEST_REGISTRY_DIR / "approvals.yaml"), "warn"
    )
    assert result.state == "fail" and any("agent_failed" in f for f in result.failures)


def test_the_gate_refuses_a_scripted_result_in_the_live_check(tmp_path):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(PUBLISHED))
    config = __import__("yaml").safe_load(gate.CONFIG.read_text())
    [spec] = [s for s in config["checks"] if s["name"] == "Complaint desk, live model"]
    result = gate.check(
        spec | {"results": str(path)}, load_approvals(TEST_REGISTRY_DIR / "approvals.yaml"), "warn"
    )
    assert result.state == "fail" and any("mode" in f for f in result.failures)


def test_the_eval_sees_a_weakened_urgency_floor(monkeypatch):
    monkeypatch.setattr(routing, "HIGH_REASONS", routing.HIGH_REASONS - {"older_american"})
    summary = evals.evaluate(PINNED, scripted_client(), REGISTRY, "scripted")["summary"]
    assert summary["must_route_floor_below_high"] > 0


def test_the_draft_field_check_is_pinned_not_read_from_the_model():
    assert evals.DRAFT_FIELDS == {
        "complaint_id",
        "text",
        "never_sent",
        "written_by",
        "prompt_version",
    }


def test_the_published_result_matches_in_full(fresh):
    assert {k: v for k, v in fresh.items() if k != "meta"} == {
        k: v for k, v in PUBLISHED.items() if k != "meta"
    }


def test_complaint_scopes_have_no_claim_page(desk_src):
    client = TestClient(create_app(desk_src), base_url="http://localhost")
    scope = f"cfpb-{PINNED[0].complaint_id}"
    assert client.get(f"/cases/{scope}.html").status_code == 404
    assert client.get(f"/api/cases/{scope}.json").status_code == 404


def test_the_desk_page_shows_a_received_but_unrouted_complaint(desk_src):
    audit = AuditLog(desk_src.audit_db)
    audit.append("cfpb-99999999", "complaint_received", "pipeline", {"complaint_id": "99999999"})
    audit.close()
    data = desk.complaint_desk(desk_src)
    [stuck] = [c for c in data["complaints"] if c["complaint_id"] == "cfpb-99999999"]
    assert stuck["queue"] is None and stuck["state"] == "claimed, not routed"
    assert data["unrouted"] == 1


def test_the_page_says_check_failed_when_a_file_wont_parse(tmp_path):
    (tmp_path / "hall_monitor").mkdir()
    (tmp_path / "hall_monitor" / "broken.py").write_text("def (:\n")
    (tmp_path / "pyproject.toml").write_text("[project\n")
    rules = sorted(f.rule for f in no_send.scan(tmp_path))
    assert rules == ["unreadable", "unreadable"]


def test_the_label_key_log_counts_the_key():
    from hall_monitor.governance.controls import ROOT

    readme = (ROOT / "data" / "cfpb" / "README.md").read_text()
    assert f"Created: {len(evals.load_label_key())} labels" in readme


# The desk page says which answers came from the scripted stand-in, and which log it reads.


def test_the_desk_page_marks_scripted_answers_and_says_so_in_a_banner(desk_src):
    data = desk.complaint_desk(desk_src)
    assert data["scripted"] == 30 and all(c["source"] == "scripted" for c in data["complaints"])
    text = desk.complaints_page(data, demo=False)  # served without --demo
    assert "Scripted answers." in text
    assert text.count(">scripted<") == 30


def test_a_model_answer_is_marked_as_the_model_s(tmp_path):
    from hall_monitor.complaints.pipeline import triage_complaint
    from tests.test_complaint_triage import _client, complaint

    db = tmp_path / "audit.sqlite"
    audit = AuditLog(db)
    triage_complaint(complaint(), registry=REGISTRY, client=_client(), audit=audit)
    audit.close()
    src = views.Sources(registry_dir=TEST_REGISTRY_DIR, audit_db=db)
    data = desk.complaint_desk(src)
    assert data["scripted"] == 0 and data["complaints"][0]["source"] == "model"
    assert "Scripted answers." not in desk.complaints_page(data, demo=False)


def test_an_entry_without_the_label_is_shown_as_unknown_not_as_the_model(tmp_path):
    db = tmp_path / "audit.sqlite"
    audit = AuditLog(db)
    audit.append(
        "cfpb-1", "complaint_routed", "pipeline", {"queue": "standard_queue", "reasons": []}
    )
    audit.close()
    src = views.Sources(registry_dir=TEST_REGISTRY_DIR, audit_db=db)
    [c] = desk.complaint_desk(src)["complaints"]
    assert c["source"] == "unknown"


def test_the_page_names_the_log_it_reads(desk_src):
    data = desk.complaint_desk(desk_src)
    assert data["audit_log"] == "audit.sqlite"
    text = desk.complaints_page(data, demo=False)
    assert "audit.sqlite" in text and "--audit-db" in text
