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
    return TestClient(create_app(src))


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
    assert TestClient(create_app(src)).get("/cases/claim_x.html").status_code == 200
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
    client = TestClient(create_app(views.Sources(audit_db=tmp_path / "audit.sqlite")))
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
