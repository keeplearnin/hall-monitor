"""HM-20: the control panel shows the governance core and can't change it."""

import ast
import re
import shutil
from pathlib import Path

import pytest
import yaml
from starlette.routing import Route
from starlette.testclient import TestClient

from hall_monitor.data import iter_claims, load_policy_records
from hall_monitor.evals import gate
from hall_monitor.governance.audit import AuditLog, ChainHead
from hall_monitor.governance.controls import load_register
from hall_monitor.kill_switch import STOPS_FILENAME, add_stop
from hall_monitor.panel import __main__ as cli
from hall_monitor.panel import demo, views
from hall_monitor.panel.app import create_app

PANEL = Path(views.__file__).parent
DEMO_STOP_REASON = "Risk scores drifted after a vendor data change"


@pytest.fixture(scope="module")
def demo_data(tmp_path_factory):
    out = tmp_path_factory.mktemp("panel") / "demo"
    built = demo.build(out, limit=12, stop_after=9)
    return out, built


@pytest.fixture
def src(demo_data):
    out, _ = demo_data
    return views.Sources(registry_dir=out / "registry", audit_db=out / "audit.sqlite", demo=True)


@pytest.fixture
def client(src):
    return TestClient(create_app(src), base_url="http://localhost")


def _all_paths(src) -> list[str]:
    paths = [
        "/index.html",
        "/agents.html",
        "/queue.html",
        "/cases.html",
        "/audit.html",
        "/evals.html",
        "/controls.html",
        "/panel.css",
    ]
    paths += [f"/api/{n}.json" for n in ("overview", "agents", "queue", "cases", "audit")]
    paths += ["/api/evals.json", "/api/controls.json"]
    paths += ["/api/leave_queue.json", "/api/leave_requests.json"]
    for r in views.leave_requests(src):
        paths += [f"/cases/{r['request_id']}.html", f"/api/cases/{r['request_id']}.json"]
    for a in views.agents(src)["agents"]:
        paths += [f"/agents/{a['id']}.html", f"/api/agents/{a['id']}.json"]
    for c in views.cases(src):
        paths += [f"/cases/{c['claim_id']}.html", f"/api/cases/{c['claim_id']}.json"]
    return paths


# --- It can't change anything ----------------------------------------------------------------


def test_every_route_only_reads(client):
    for route in create_app().routes:
        assert isinstance(route, Route)
        assert route.methods <= {"GET", "HEAD"}, route.path
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/agents.html").status_code == 405


def test_reading_every_page_leaves_every_file_as_it_was(demo_data, src, client):
    out, _ = demo_data
    files = sorted(p for p in out.rglob("*") if p.is_file())
    before = {p: views.file_sha256(p) for p in files}
    for path in _all_paths(src):
        assert client.get(path).status_code == 200, path
    assert {p: views.file_sha256(p) for p in sorted(out.rglob("*")) if p.is_file()} == before


def test_the_panel_opens_the_audit_log_read_only_and_never_the_checkpoints():
    for file in ("views.py", "app.py", "pages.py", "__main__.py"):
        tree = ast.parse((PANEL / file).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "AuditLog":
                assert any(
                    k.arg == "read_only" and getattr(k.value, "value", None) is True
                    for k in node.keywords
                ), f"{file}: AuditLog opened writable"
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert "hall_monitor.workflow" not in modules, f"{file} reaches the checkpoints"
        assert not imported & {"add_stop", "current_approvals", "open_workflow"}, file


def test_an_audit_log_without_a_key_gets_none_from_the_panel(tmp_path):
    log = AuditLog(tmp_path / "audit.sqlite")
    log.append("claim_x", "claim_submitted", "channel:email", {"attachments": 0})
    log.close()
    (tmp_path / "audit.key").unlink()
    src = views.Sources(audit_db=tmp_path / "audit.sqlite")
    assert (
        TestClient(create_app(src), base_url="http://localhost")
        .get("/cases/claim_x.html")
        .status_code
        == 200
    )
    assert not (tmp_path / "audit.key").exists()


# --- Nothing personal ------------------------------------------------------------------------


def test_no_page_shows_claim_text_stop_reasons_adjuster_reasons_or_signatures(src, client):
    claims = list(iter_claims())[:12]
    personal = {c.narrative[:40] for c in claims} | {DEMO_STOP_REASON, "duplicate of another"}
    personal |= {r.insured_name for r in load_policy_records().values()}
    for path in _all_paths(src):
        text = client.get(path).text
        for value in personal:
            assert value not in text, f"{path} shows {value!r}"
    for c in views.cases(src):
        trace = client.get(f"/api/cases/{c['claim_id']}.json").json()
        for entry in trace["entries"]:
            assert entry["payload"].get("signature") in (None, "present (Ed25519)")


def test_values_from_the_log_are_escaped(tmp_path):
    log = AuditLog(tmp_path / "audit.sqlite")
    log.append("<script>x</script>", "claim_submitted", "<b>actor</b>", {"note": "<img src=x>"})
    log.close()
    client = TestClient(
        create_app(views.Sources(audit_db=tmp_path / "audit.sqlite")), base_url="http://localhost"
    )
    page = client.get("/cases.html").text
    assert "<script>x</script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page


def test_pages_allow_no_scripts_and_no_other_origins(client):
    response = client.get("/index.html")
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "script-src" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in response.text


def test_requests_for_another_host_are_refused(client):
    """No sign-in yet: only requests addressed to this machine, so DNS rebinding can't reach it."""
    assert client.get("/index.html", headers={"host": "panel.example.com"}).status_code == 400
    assert client.get("/index.html", headers={"host": "localhost:8765"}).status_code == 200


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "panel.example.com"])
def test_serve_refuses_to_listen_beyond_this_machine(host, capsys):
    assert cli.main(["serve", "--host", host]) == 2
    assert "no sign-in" in capsys.readouterr().err


# --- It agrees with the code that enforces each control --------------------------------------


def test_the_demo_story_is_what_the_overview_shows(demo_data, src):
    _, built = demo_data
    m = views.overview(src)["workflows"][0]["metrics"]
    assert m["cases"] == m["triaged"] == 12
    assert m["decided_by_a_person"] == built["decided_by_adjusters"]
    assert m["waiting_for_a_person"] == len(built["waiting"])
    assert [c["claim_id"] for c in views.review_queue(src)] == built["waiting"]
    assert m["stopped_cases"] == 3  # the claims after the stop
    assert m["overrides"] == 1 and m["rejected_decisions"] == 1
    # Reasons are counted without each case's figures.
    assert not any(re.match(r"payout \d", reason) for reason, _ in m["routing_reasons"])


def _registry_copy(demo_data, tmp_path) -> Path:
    out, _ = demo_data
    registry = tmp_path / "registry"
    shutil.copytree(out / "registry", registry)
    return registry


def test_an_agent_state_is_the_registry_s_own_answer(demo_data, src, tmp_path):
    assert {a["id"]: a["state"] for a in views.agents(src)["agents"]}["risk_fraud"] == "stopped"
    # A new stop shows at once: the registry's own check decides, reading the stop list.
    registry = _registry_copy(demo_data, tmp_path)
    add_stop(registry / STOPS_FILENAME, agent="intake", by="own-301", reason="test")
    states = {
        a["id"]: a["state"] for a in views.agents(views.Sources(registry_dir=registry))["agents"]
    }
    assert states["intake"] == "stopped" and states["critic"] == "may run"


def test_an_unreadable_stop_list_shows_every_agent_refused(tmp_path, demo_data):
    out, _ = demo_data
    registry = _registry_copy(demo_data, tmp_path)
    (registry / STOPS_FILENAME).write_text("stops: [oops\n")
    data = views.agents(views.Sources(registry_dir=registry, audit_db=out / "audit.sqlite"))
    assert {a["state"] for a in data["agents"]} == {"refused"}
    assert "every agent is refused" in data["problem"]


def test_a_changed_manifest_shows_as_unapproved(tmp_path, demo_data):
    registry = _registry_copy(demo_data, tmp_path)
    manifest = registry / "critic.yaml"
    manifest.write_text(manifest.read_text().replace("purpose:", "purpose: (edited)", 1))
    rows = views.agents(views.Sources(registry_dir=registry))["agents"]
    critic = next(a for a in rows if a["id"] == "critic")
    assert critic["state"] == "unapproved" and not critic["manifest_approved"]


def test_audit_integrity_checks_an_anchored_head(src, client):
    head = views.audit_integrity(src)["head"]
    good = client.get(f"/api/audit.json?seq={head['seq']}&hash={head['entry_hash']}").json()
    assert good["ok"] and good["anchor"] == head
    bad = client.get(f"/api/audit.json?seq={head['seq']}&hash={'0' * 64}").json()
    assert not bad["ok"] and "anchored head" in bad["problems"][0]
    assert client.get("/audit.html?seq=1&hash=nothex").status_code == 400
    assert views.audit_integrity(src, ChainHead(head["seq"], head["entry_hash"]))["ok"]


def test_the_eval_view_is_the_gate_s_result(src):
    data = views.evals(src)
    ok, results = gate.run_gate()
    assert data["passed"] == ok
    assert [(c["name"], c["state"]) for c in data["checks"]] == [(r.name, r.state) for r in results]


def test_the_controls_view_lists_the_whole_register(src):
    ids = [c["id"] for c in views.controls(src)["controls"]]
    assert ids == [c["id"] for c in load_register()["controls"]]
    assert "HM-20" in ids


def test_unknown_agents_and_cases_are_not_found(client):
    assert client.get("/agents/nobody.html").status_code == 404
    assert client.get("/api/cases/claim_999.json").status_code == 404
    assert client.get("/api/nothing.json").status_code == 404


# --- The snapshot ----------------------------------------------------------------------------


def test_a_snapshot_saves_every_page_and_every_link_resolves(src, tmp_path):
    saved = cli.snapshot(src, tmp_path)
    assert len(saved) == len(_all_paths(src))
    for page in tmp_path.rglob("*.html"):
        for href in re.findall(r'href="([^"#?]+)"', page.read_text()):
            assert (page.parent / href).resolve().exists(), f"{page.name} links to {href}"


def test_the_eval_view_judges_results_against_the_panel_s_own_approvals(tmp_path, demo_data):
    """Approvals that match what the baseline scored make that result current for this panel,
    while the repository's approvals still find it stale."""
    registry = _registry_copy(demo_data, tmp_path)
    scored = views.evals(views.Sources())["checks"][0]["agents"]
    approvals = {
        a["agent_id"]: {"manifest_digest": a["manifest_digest"], "version": a["agent_version"]}
        for a in scored
    }
    (registry / "approvals.yaml").write_text(yaml.safe_dump({"agents": approvals}))
    assert views.evals(views.Sources(registry_dir=registry))["checks"][0]["state"] == "pass"
    assert views.evals(views.Sources())["checks"][0]["state"] == "stale"


# --- Review 7 findings -----------------------------------------------------------------------


def _log_with(tmp_path, entries) -> views.Sources:
    log = AuditLog(tmp_path / "audit.sqlite")
    for claim_id, event, actor, payload in entries:
        log.append(claim_id, event, actor, payload)
    log.close()
    return views.Sources(audit_db=tmp_path / "audit.sqlite")


def _local(src) -> TestClient:
    return TestClient(create_app(src), base_url="http://localhost")


MODEL_WRITTEN = "Jane Doe, broken leg in Lisbon"


def test_model_written_text_in_the_log_never_reaches_a_page(tmp_path):
    """Tool arguments, tool errors, corrected policy numbers and revision values are written by
    the model; the panel shows an allow-list of fields per event, and reasons only when they
    match the code's own templates."""
    src = _log_with(
        tmp_path,
        [
            (
                "claim_x",
                "tool_call",
                "risk_fraud",
                {
                    "actor": "risk_fraud",
                    "tool": "policy_lookup",
                    "arguments": {"policy_number": f"WTP-ESS-999999 {MODEL_WRITTEN}"},
                    "allowed": False,
                    "result_sha256": None,
                    "error": f"policy WTP-ESS-999999 {MODEL_WRITTEN} is outside the claim",
                    "identifiers_redacted": {},
                    "seconds": 0.01,
                },
            ),
            (
                "claim_x",
                "tool_call",
                "intake",
                {"actor": "intake", "tool": MODEL_WRITTEN, "arguments": {}, "allowed": False},
            ),
            (
                "claim_x",
                "code_correction",
                "pipeline",
                {
                    "correction": f"policy_number '{MODEL_WRITTEN}' replaced by 'WTP-ESS-100001', "
                    "the only valid policy number in the claim text"
                },
            ),
            (
                "claim_x",
                "revision",
                "intake",
                {
                    "round": 1,
                    "changed": {
                        "policy_number": [MODEL_WRITTEN, "WTP-ESS-100001"],
                        "total_claimed": [450.0, 540.0],
                    },
                },
            ),
            (
                "claim_x",
                "triage_result",
                "pipeline",
                {
                    "route": "adjuster",
                    "routing_reasons": [
                        "payout 3,000.00 above 2,500 threshold",
                        f"pipeline stopped: hall pass refused: agent 'intake' may not call tool "
                        f"'{MODEL_WRITTEN}'",
                    ],
                    "stopped_reason": f"hall pass refused: agent 'intake' may not call tool "
                    f"'{MODEL_WRITTEN}'",
                    "cost_usd": 0.01,
                    "unknown_field": MODEL_WRITTEN,
                },
            ),
            ("claim_x", "something_new", "pipeline", {"note": MODEL_WRITTEN}),
        ],
    )
    client = _local(src)
    for path in ("/cases/claim_x.html", "/api/cases/claim_x.json", "/index.html", "/cases.html"):
        text = client.get(path).text
        assert "Jane Doe" not in text and "WTP-ESS-999999" not in text, path
        assert "WTP-ESS-100001" not in text, path  # a policy number is the policyholder's
    trace = client.get("/api/cases/claim_x.json").json()
    shown = [e["payload"] for e in trace["entries"]]
    assert shown[0]["arguments"] == "withheld (1)" and shown[0]["error"] == "withheld"
    assert shown[3]["changed"]["total_claimed"] == [450.0, 540.0]
    assert shown[4]["routing_reasons"][0] == "payout 3,000.00 above 2,500 threshold"
    assert "withheld" in shown[4]["routing_reasons"][1]


@pytest.mark.parametrize(
    "reason",
    [
        "claim is in EUR, not USD: amounts can't be compared",
        "no policy record to check the claim against",
        "claimant is not the insured named on the policy",
        "no incident date to check against the coverage period",
        "incident date is outside the coverage period",
        "no claim text to check the claimed amounts against",
        "claimed amounts not in the claim documents in USD: 1.00, 2,023.00",
        "claimed items (450.00) don't add up to the 540.00 claimed",
        "recommended request information",
        "nothing covered (a denial)",
        "payout 600.00 exceeds the 540.00 claimed",
        "payout 3,000.00 above 2,500 threshold",
        "fraud flags: duplicate_receipt, prior_similar_claim",
        "high risk",
        "critic still disagrees after revisions",
        "critic changed coverage_check.covered_amount, intake.total_claimed",
        "claim text addresses the AI: override_instructions, names_internal_tools",
        "pipeline stopped: agent refused or returned no usable output: intake declined claim c_1",
        "pipeline stopped: APIError from the model call",
        "pipeline stopped: hall pass refused: agent 'risk_fraud' is stopped (stop-0001 by own-302)",
        "pipeline stopped: no policy record found for the policy number on the claim",
        "pipeline stopped: unexpected KeyError",
    ],
)
def test_every_reason_the_code_writes_is_shown(reason):
    assert views.shown_reason(reason) == reason


def test_reasons_are_counted_without_their_figures():
    kinds = {
        views.reason_kind(r)
        for r in (
            "payout 600.00 exceeds the 540.00 claimed",
            "payout 700.00 exceeds the 650.00 claimed",
        )
    }
    assert kinds == {"payout exceeds the amount claimed"}
    assert views.reason_kind("claimed amounts not in the claim documents in USD: 1.00") == (
        "claimed amounts not in the claim documents in USD"
    )
    assert views.reason_kind("claimed items (1.00) don't add up to the 2.00 claimed") == (
        "claimed items don't add up to the total claimed"
    )
    assert views.reason_kind(
        "pipeline stopped: agent refused or returned no usable output: "
        "intake declined claim claim_007"
    ) == ("pipeline stopped: agent refused or returned no usable output")


def test_a_snapshot_never_writes_outside_its_folder(tmp_path):
    src = _log_with(
        tmp_path,
        [(cid, "claim_submitted", "channel:email", {}) for cid in ("../../../index", "a?b", "")]
        + [("claim_ok", "claim_submitted", "channel:email", {})],
    )
    out = tmp_path / "snap" / "deep"
    cli.snapshot(src, out)
    written = [p for p in tmp_path.rglob("*") if p.is_file() and p.suffix in (".html", ".json")]
    assert all(out in p.parents for p in written), written
    assert (out / "cases" / "claim_ok.html").exists()


def test_a_damaged_audit_log_shows_as_damaged_not_as_an_error(tmp_path):
    import sqlite3

    src = _log_with(tmp_path, [("claim_x", "claim_submitted", "channel:email", {})])
    db = sqlite3.connect(tmp_path / "audit.sqlite")
    db.execute("DROP TRIGGER audit_log_no_update")
    db.execute("UPDATE audit_log SET payload = 'not json'")
    db.commit()
    db.close()
    client = _local(src)
    for path in ("/index.html", "/cases.html", "/queue.html", "/audit.html", "/cases/claim_x.html"):
        assert client.get(path).status_code == 200, path
    assert not views.overview(src)["audit"]["ok"]
    assert "can't be read" in views.overview(src)["audit_problem"]
    (tmp_path / "garbage.sqlite").write_bytes(b"this is not a database at all, not even close")
    garbage = views.Sources(audit_db=tmp_path / "garbage.sqlite")
    for path in ("/index.html", "/cases.html", "/audit.html"):
        assert _local(garbage).get(path).status_code == 200, path
    assert not views.audit_integrity(garbage)["ok"]


def test_the_queue_holds_only_claims_routed_to_a_person(tmp_path):
    src = _log_with(
        tmp_path,
        [
            ("claim_a", "triage_result", "pipeline", {"route": "auto", "routing_reasons": []}),
            ("claim_a", "evidence_pack", "pipeline", {"evidence_sha256": "0" * 64}),
            ("claim_b", "triage_result", "pipeline", {"route": "adjuster", "routing_reasons": []}),
            ("claim_b", "evidence_pack", "pipeline", {"evidence_sha256": "1" * 64}),
        ],
    )
    assert [c["claim_id"] for c in views.review_queue(src)] == ["claim_b"]
    states = {c["claim_id"]: c["state"] for c in views.cases(src)}
    assert states["claim_a"] == "routed automatically, no decision recorded"


def test_the_demo_builds_from_a_registry_whose_approvals_clear_a_stop(tmp_path, monkeypatch):
    registry = tmp_path / "shipped"
    shutil.copytree(demo.DEFAULT_REGISTRY_DIR, registry, ignore=shutil.ignore_patterns(".*"))
    add_stop(registry / STOPS_FILENAME, agent="critic", by="own-301", reason="test")
    approvals = yaml.safe_load((registry / "approvals.yaml").read_text())
    approvals["agents"]["critic"]["cleared_stops"] = ["stop-0001"]
    (registry / "approvals.yaml").write_text(yaml.safe_dump(approvals))
    monkeypatch.setattr(demo, "DEFAULT_REGISTRY_DIR", registry)
    built = demo.build(tmp_path / "demo", limit=6, stop_after=5)
    assert built["claims"] == 6


def test_every_audit_log_the_panel_opens_is_read_only(src, monkeypatch):
    """A writable open would leave the files unchanged too, so check the opens themselves."""
    import sqlite3

    real = sqlite3.connect
    opened = []

    def spy(target, *args, **kwargs):
        opened.append(str(target))
        return real(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    client = TestClient(create_app(src), base_url="http://localhost")
    for path in _all_paths(src):
        client.get(path)
    assert opened and all(t.startswith("file:") and t.endswith("?mode=ro") for t in opened)


def test_only_this_machine_s_names_are_accepted():
    app = create_app(views.Sources())
    for host in ("testserver", "localhost.", "[::1]:8765", "evil.example"):
        assert TestClient(app).get("/index.html", headers={"host": host}).status_code == 400
    assert cli.main(["serve", "--host", "::1"]) == 2


def test_a_non_ascii_digit_anchor_is_refused_not_an_error(client):
    assert client.get(f"/audit.html?seq=%C2%B2&hash={'a' * 64}").status_code == 400


def test_a_broken_approvals_file_shows_on_the_eval_page(tmp_path, demo_data):
    registry = _registry_copy(demo_data, tmp_path)
    (registry / "approvals.yaml").write_text("- not a mapping\n")
    client = _local(views.Sources(registry_dir=registry))
    for path in ("/evals.html", "/agents.html", "/agents/critic.html", "/index.html"):
        assert client.get(path).status_code in (200, 404), path
    assert views.evals(views.Sources(registry_dir=registry))["problem"]


def test_saved_pages_carry_their_own_security_policy(src, tmp_path):
    cli.snapshot(src, tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert '<meta http-equiv="Content-Security-Policy"' in page and "default-src 'none'" in page


# --- Re-review of the review 7 fixes ---------------------------------------------------------


def test_every_reason_the_code_generates_is_shown(tmp_path, demo_data):
    """Reasons built by the real routing rule and the real refusals, so a changed message fails
    here instead of being withheld unnoticed."""
    from hall_monitor.gateway.cost import UnpricedModelError, call_cost
    from hall_monitor.gateway.injection import SIGNALS
    from hall_monitor.governance.routing import route_claim, route_stopped
    from hall_monitor.models import (
        AgentCall,
        CoverageAssessment,
        CoverageDecision,
        NextStep,
        Recommendation,
        RiskAssessment,
        RiskFlag,
        RiskLevel,
    )
    from hall_monitor.registry import DataClass, Registry, RegistryError

    stops = []

    def refusal(registry_dir, check):
        try:
            check(Registry.from_dir(registry_dir))
        except RegistryError as exc:
            stops.append(f"hall pass refused: {exc}")

    stopped = _registry_copy(demo_data, tmp_path / "a")
    add_stop(stopped / STOPS_FILENAME, agent="risk_fraud", by="own-302", reason="x")
    refusal(stopped, lambda r: r.require("risk_fraud"))
    refusal(stopped, lambda r: r.require("intake", prompt_sha256="ab" * 32))
    refusal(stopped, lambda r: r.check_data("intake", frozenset(DataClass)))
    refusal(stopped, lambda r: r.require("nobody"))
    broken = _registry_copy(demo_data, tmp_path / "b")
    (broken / STOPS_FILENAME).write_text("stops: [1")
    refusal(broken, lambda r: r.require("intake"))
    try:
        call_cost(
            AgentCall(
                agent_id="intake",
                agent_version="1",
                model="claude-x-9",
                manifest_digest="a",
                prompt_version="1",
                system_prompt_sha256="a",
                input_sha256="a",
                request_ids=[],
                stop_reason=None,
                input_tokens=1,
                output_tokens=1,
                seconds=0,
            )
        )
    except UnpricedModelError as exc:
        stops.append(f"unexpected KeyError; cost unknown: {exc}")
    reasons = [route_stopped(m).reasons[0] for m in stops]

    claim = next(iter_claims())
    truth = demo.load_ground_truth(claim.claim_id)
    extraction = truth.extraction.model_copy(
        update={"currency": "EUR", "total_claimed": 1234567.891, "incident_date": None}
    )
    coverage = CoverageAssessment(
        decision=CoverageDecision.NOT_COVERED,
        applicable_clauses=[],
        covered_amount=9999999.5,
        reasoning="r",
    )
    risk = RiskAssessment(level=RiskLevel.HIGH, flags=list(RiskFlag), reasoning="r")
    recommendation = Recommendation(next_step=NextStep.REQUEST_INFORMATION, reasons=[])
    policy = next(iter(load_policy_records().values()))
    reasons += route_claim(
        extraction,
        coverage,
        risk,
        recommendation,
        critic_unresolved=True,
        critic_changed=["coverage_check.covered_amount", "intake.incident_date"],
        injection_signals=list(SIGNALS),
        policy=policy,
        claim_text="no amounts here",
    ).reasons
    reasons += route_claim(extraction, coverage, risk, recommendation).reasons
    assert len(reasons) > 15
    assert [r for r in reasons if views.shown_reason(r) != r] == []


WRONG_TYPES = (5, 1.5, True, "text", [1, "a"], {"a": {"b": 1}}, None)


@pytest.mark.parametrize("value", WRONG_TYPES, ids=repr)
def test_an_entry_with_fields_of_the_wrong_type_shows_as_an_entry_not_an_error(tmp_path, value):
    """A tampered entry that still parses: every page still renders, and the audit page says
    the chain doesn't verify (it would, once someone changed a written entry)."""
    keys = {
        "claim_submitted": ("attachments", "identifiers_redacted", "injection_signals"),
        "agent_call": ("agent_id", "stop_reason", "request_ids", "input_tokens"),
        "tool_call": ("tool", "arguments", "error", "allowed"),
        "critic_review": ("issues",),
        "revision": ("round", "changed"),
        "code_correction": ("correction",),
        "triage_result": (
            "route",
            "routing_reasons",
            "stopped_reason",
            "covered_amount",
            "cost_usd",
            "models",
        ),
        "evidence_pack": ("evidence_sha256", "result_sha256"),
        "decision_rejected": ("rejected_because",),
        "final_decision": ("decided_by", "payout_usd", "overrode_ai", "routing_reasons"),
    }
    src = _log_with(
        tmp_path,
        [
            ("claim_x", event, "pipeline", dict.fromkeys(fields, value))
            for event, fields in keys.items()
        ],
    )
    client = _local(src)
    for path in ("/index.html", "/cases.html", "/queue.html", "/cases/claim_x.html"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/cases/claim_x.json").status_code == 200


def test_text_in_a_tampered_field_is_withheld(tmp_path):
    src = _log_with(
        tmp_path,
        [
            ("claim_x", "claim_submitted", "channel:email", {"attachments": {"a": MODEL_WRITTEN}}),
            ("claim_x", "agent_call", "intake", {"stop_reason": MODEL_WRITTEN}),
        ],
    )
    assert "Jane Doe" not in _local(src).get("/cases/claim_x.html").text


def test_serve_listens_only_where_the_host_check_answers(capsys):
    assert cli.main(["serve", "--host", "127.0.0.2"]) == 2


def test_a_snapshot_refuses_ids_that_differ_only_in_case(tmp_path):
    src = _log_with(
        tmp_path,
        [(cid, "claim_submitted", "channel:email", {}) for cid in ("CASE-A", "case-a")],
    )
    with pytest.raises(ValueError, match="differ only in case"):
        cli.snapshot(src, tmp_path / "snap")


def test_a_case_s_route_is_the_one_its_evidence_pack_records(tmp_path):
    """Two runs at once: the second logs its result, then loses the race for the evidence pack.
    The pack records which result stands, as the workflow reads it."""
    src = _log_with(
        tmp_path,
        [
            (
                "claim_x",
                "triage_result",
                "pipeline",
                {"route": "adjuster", "result_sha256": "a" * 64},
            ),
            (
                "claim_x",
                "evidence_pack",
                "pipeline",
                {"evidence_sha256": "e" * 64, "result_sha256": "a" * 64},
            ),
            ("claim_x", "triage_result", "pipeline", {"route": "auto", "result_sha256": "b" * 64}),
        ],
    )
    case = views.cases(src)[0]
    assert case["route"] == "adjuster" and case["state"] == "waiting for a person"


# --- Leave requests --------------------------------------------------------------------------


def test_the_hr_overview_tells_the_demo_s_leave_story(demo_data, src):
    _, built = demo_data
    m = next(w for w in views.overview(src)["workflows"] if w["id"] == "hr_leave")["metrics"]
    outcomes = list(built["leave_outcomes"].values())
    assert m["requests"] == m["prepared"] == built["leave_requests"] == 28
    assert m["decided"] == outcomes.count("approve") + outcomes.count("deny") == 9
    assert m["approved"] == 7 and m["denied"] == 2
    assert m["information_requested"] == 1 and m["refused_decisions"] == 1
    assert m["waiting_for_hr"] == len(built["leave_waiting"]) == 19
    assert m["specialist_sign_off"] == 8 and m["hr_review"] == 20
    assert [r["request_id"] for r in views.leave_queue(src)] == sorted(built["leave_waiting"])


def test_leave_requests_never_appear_as_claims(src):
    assert not any(c["claim_id"].startswith("lr_") for c in views.cases(src))
    assert not any(c["claim_id"].startswith("lr_") for c in views.review_queue(src))
    m = next(w for w in views.overview(src)["workflows"] if w["id"] == "travel_claims")
    assert m["metrics"]["cases"] == 12


def test_no_page_shows_an_employee_s_message_the_pack_or_a_decision_s_reason(src, client):
    from hall_monitor.hr.data import load_messages

    private = {m.text[:50] for m in load_messages()}
    private |= {d[4] for d in demo.LEAVE_DECISIONS if d[4]}
    private |= {"[SSN REDACTED]", "123-45-6789", "Alex Moreno", "Jamie Okoro", "Taylor Brooks"}
    for path in _all_paths(src):
        text = client.get(path).text
        for value in private:
            assert value not in text, f"{path} shows {value!r}"


def test_a_leave_request_s_page_shows_its_decision(client):
    page = client.get("/cases/lr_009.html").text
    assert "Leave request lr_009" in page and "approve, 2.0 weeks" in page and "hr_director" in page
    queue = client.get("/queue.html").text
    assert "lr_019" in queue and "lr_009" not in queue


def test_every_refusal_the_sign_off_can_give_is_shown_not_withheld(tmp_path, demo_data):
    """Refusals come from the code (hr/signoff.py); the panel shows them only in its own
    templates, so one left out would be withheld. Drive each refusal and check."""
    from hall_monitor.governance.signing import demo_private_key
    from hall_monitor.hr import signoff

    out, _ = demo_data
    db = tmp_path / "audit.sqlite"
    shutil.copy(out / "audit.sqlite", db)
    shutil.copy(out / "audit.key", tmp_path / "audit.key")
    log = AuditLog(db)
    staff = signoff.load_staff()
    reason = "Checked the request and the records against the policy."
    attempts = [
        ("lr_022", "hrs-999", "approve", 4, ""),
        ("lr_022", "hrs-101", "approve", 0, ""),
        ("lr_022", "hrs-101", "approve", 5, ""),
        ("lr_022", "hrs-101", "approve", 2, reason),
        ("lr_022", "hrs-201", "approve", 2, ""),
        ("lr_022", "hrs-101", "deny", 0, reason),
        ("lr_022", "hrs-201", "deny", 0, "short"),
        ("lr_022", "hrs-201", "deny", 1, reason),
        ("lr_022", "hrs-201", "extend", 1, reason),
        ("lr_022", "hrs-301", "extend", 4, "short"),
        ("lr_022", "hrs-301", "extend", 4, reason),
        ("lr_022", "hrs-101", "request_information", 1, ""),
        ("lr_012", "hrs-201", "approve", 4, reason),
        ("lr_004", "hrs-301", "approve", 4, reason),
        ("lr_013", "hrs-101", "approve", 4, ""),
        ("lr_017", "hrs-201", "approve", 6, reason),
        ("lr_011", "hrs-101", "approve", 13, reason),
        ("lr_004", "hrs-301", "extend", 4, reason),
        ("lr_009", "hrs-301", "approve", 2, reason),  # already decided
    ]
    for request_id, staff_id, outcome, weeks, why in attempts:
        digest = signoff.evidence(log, request_id)[0]
        decision = signoff.LeaveDecision(
            staff_id=staff_id, outcome=outcome, weeks=weeks, reason=why, evidence_sha256=digest
        ).signed(request_id, demo_private_key(staff_id if staff_id != "hrs-999" else "hrs-101"))
        with pytest.raises(signoff.DecisionRefused):
            signoff.decide(log, request_id, decision, staff)
    wrong_hash = signoff.LeaveDecision(
        staff_id="hrs-101", outcome="approve", weeks=4, evidence_sha256="0" * 64
    ).signed("lr_022", demo_private_key("hrs-101"))
    with pytest.raises(signoff.DecisionRefused):
        signoff.decide(log, "lr_022", wrong_hash, staff)
    log.close()
    refusals = [
        views.shown_payload(e["event"], e["payload"])["refused_because"]
        for e in AuditLog(db, read_only=True).entries()
        if e["event"] == "leave_decision_refused"
    ]
    assert len(refusals) >= len(attempts) + 1
    assert views.WITHHELD not in refusals, [r for r in refusals if r == views.WITHHELD]


def test_hr_fields_the_code_doesn_t_write_are_withheld(tmp_path):
    log = AuditLog(tmp_path / "audit.sqlite")
    log.append(
        "lr_x",
        "leave_prepared",
        "pipeline",
        {
            "route": "hr_review",
            "decided_by": "hr_manager",
            "reasons": ["short_service", "Jane Doe"],
            "stopped_reason": "Jane Doe asked",
            "explanation_problem": "Jane Doe",
            "note": "Jane Doe",
        },
    )
    log.append("lr_x", "leave_decision_refused", "hr:?", {"refused_because": "Jane Doe said no"})
    log.close()
    src = views.Sources(audit_db=tmp_path / "audit.sqlite")
    client = TestClient(create_app(src), base_url="http://localhost")
    for path in ("/cases/lr_x.html", "/api/cases/lr_x.json", "/index.html", "/cases.html"):
        assert "Jane Doe" not in client.get(path).text, path
