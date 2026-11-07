"""The leave submission app: it adds requests to HR's queue, outside the read-only panel."""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from hall_monitor.governance.audit import AuditLog
from hall_monitor.hr import signoff, submit
from hall_monitor.hr.data import load_messages
from hall_monitor.panel import views
from tests.fakes import TEST_REGISTRY_DIR, fake_client
from tests.test_hr_agents import _client

MESSAGES = {m.id: m for m in load_messages()}
SAME_ORIGIN = {"Origin": "http://localhost"}


def _app(tmp_path, *, live=False, client=None, max_requests=20) -> TestClient:
    app = submit.create_app(
        live=live,
        audit_db=tmp_path / "audit.sqlite",
        registry_dir=TEST_REGISTRY_DIR,
        client=client,
        max_requests=max_requests,
    )
    return TestClient(app, base_url="http://localhost", follow_redirects=False)


def _token(client: TestClient) -> str:
    return re.search(r'name="token" value="([^"]+)"', client.get("/").text).group(1)


def _submit(client: TestClient, headers=SAME_ORIGIN, **fields):
    return client.post("/submit", data={"token": _token(client), **fields}, headers=headers)


def _entries(tmp_path) -> list[dict]:
    if not (tmp_path / "audit.sqlite").exists():
        return []
    log = AuditLog(tmp_path / "audit.sqlite", read_only=True)
    try:
        return log.entries()
    finally:
        log.close()


def _status(tmp_path, request_id: str) -> dict:
    log = AuditLog(tmp_path / "audit.sqlite", read_only=True)
    try:
        return signoff.status(log, request_id)
    finally:
        log.close()


def _request_id(response) -> str:
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[1]


def test_a_demo_submission_joins_hr_s_queue_in_the_panel(tmp_path):
    client = _app(tmp_path)
    response = _submit(client, message_id="lr_001")
    request_id = _request_id(response)
    assert re.fullmatch(r"lr_web_[0-9a-f]{8}", request_id)
    assert _status(tmp_path, request_id)["state"] == "waiting for HR"
    queue = views.leave_queue(
        views.Sources(audit_db=tmp_path / "audit.sqlite", registry_dir=TEST_REGISTRY_DIR)
    )
    assert request_id in str(queue)
    page = client.get(response.headers["location"])
    assert page.status_code == 200 and "waiting for HR" in page.text


def test_each_submission_is_a_new_request(tmp_path):
    client = _app(tmp_path)
    first = _request_id(_submit(client, message_id="lr_001"))
    second = _request_id(_submit(client, message_id="lr_001"))
    assert first != second
    assert {_status(tmp_path, r)["state"] for r in (first, second)} == {"waiting for HR"}


def test_demo_mode_takes_only_a_synthetic_message(tmp_path):
    """The scripted model answers from the answer key, so demo mode can't take free text."""
    client = _app(tmp_path)
    form = client.get("/").text
    assert "lr_001" in form and "<textarea" not in form
    assert _submit(client, message_id="lr_999").status_code == 400
    assert _submit(client, employee_id="e101", text="Leave please").status_code == 400
    assert _entries(tmp_path) == []


def test_a_live_submission_runs_the_pipeline_and_logs_no_text(tmp_path):
    client = _app(tmp_path, live=True, client=_client())
    message = MESSAGES["lr_001"]
    request_id = _request_id(_submit(client, employee_id=message.employee_id, text=message.text))
    assert _status(tmp_path, request_id)["state"] == "waiting for HR"
    everything = str(_entries(tmp_path))
    assert "knee" not in everything and "Dana" not in everything


def test_live_mode_refuses_an_unknown_employee_and_empty_or_overlong_text(tmp_path):
    client = _app(tmp_path, live=True, client=_client())
    text = MESSAGES["lr_001"].text
    assert _submit(client, employee_id="e999", text=text).status_code == 400
    assert _submit(client, employee_id="e101", text="   ").status_code == 400
    assert _submit(client, employee_id="e101", text="x" * 10_001).status_code == 400
    assert _submit(client, employee_id="e101").status_code == 400
    assert _entries(tmp_path) == []


@pytest.mark.parametrize(
    "token, headers",
    [
        (None, SAME_ORIGIN),
        ("not-the-token", SAME_ORIGIN),
        ("form", {"Origin": "http://evil.example"}),
        ("form", {}),
        ("form", SAME_ORIGIN | {"Sec-Fetch-Site": "cross-site"}),
    ],
    ids=["no token", "wrong token", "other origin", "no origin", "cross-site fetch"],
)
def test_a_submission_from_another_site_is_refused(tmp_path, token, headers):
    """A page elsewhere can make the browser post a form to this machine; it can't read the
    form's token, and the browser says where the post came from."""
    client = _app(tmp_path)
    data = {"message_id": "lr_001"}
    if token is not None:
        data["token"] = _token(client) if token == "form" else token
    assert client.post("/submit", data=data, headers=headers).status_code == 403
    assert _entries(tmp_path) == []


def test_only_requests_addressed_to_this_machine_are_answered(tmp_path):
    app = submit.create_app(
        live=False, audit_db=tmp_path / "audit.sqlite", registry_dir=TEST_REGISTRY_DIR
    )
    assert TestClient(app, base_url="http://evil.example").get("/").status_code == 400


def test_a_large_body_is_refused_before_it_is_read(tmp_path):
    client = _app(tmp_path, live=True, client=_client())
    response = _submit(client, employee_id="e101", text="x" * (submit.MAX_BODY + 1))
    assert response.status_code == 413
    assert _entries(tmp_path) == []


def test_a_chunked_body_over_the_limit_is_refused(tmp_path):
    """With no declared length, the limit is enforced while the body is read."""
    client = _app(tmp_path, live=True, client=_client())
    token = _token(client)

    def body():
        yield f"token={token}&employee_id=e101&text=".encode()
        for _ in range(submit.MAX_BODY // 1024 + 1):
            yield b"x" * 1024

    response = client.post(
        "/submit",
        content=body(),
        headers=SAME_ORIGIN | {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 413
    assert _entries(tmp_path) == []


def test_a_message_within_the_limit_is_never_too_large_to_send(tmp_path):
    """URL-encoding makes a non-ASCII character up to 9 bytes, and a browser sends a textarea's
    line breaks as CRLF, which its maxlength counts as one character."""
    client = _app(tmp_path, live=True, client=_client(), max_requests=5)
    _request_id(_submit(client, employee_id="e101", text="é" * 6_000))
    lead = MESSAGES["lr_001"].text
    text = lead + "\r\n" * 10 + "x" * (10_000 - len(lead) - 10)
    _request_id(_submit(client, employee_id="e101", text=text))


def test_only_a_form_post_is_taken(tmp_path):
    """A text/plain post needs no preflight from another site; it is refused whatever it holds."""
    client = _app(tmp_path)
    token = _token(client)
    plain = client.post(
        "/submit",
        content=f"token={token}&message_id=lr_001",
        headers=SAME_ORIGIN | {"Content-Type": "text/plain"},
    )
    assert plain.status_code == 415
    repeated = client.post(
        "/submit",
        content=f"token={token}&token={token}&message_id=lr_001",
        headers=SAME_ORIGIN | {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert repeated.status_code == 400
    assert _entries(tmp_path) == []


def test_a_refused_submission_doesn_t_use_up_the_spend_cap(tmp_path):
    client = _app(tmp_path, live=True, client=_client(), max_requests=1)
    message = MESSAGES["lr_001"]
    wrong = {"token": "not-the-token", "employee_id": message.employee_id, "text": message.text}
    assert client.post("/submit", data=wrong, headers=SAME_ORIGIN).status_code == 403
    assert _submit(client, employee_id="e999", text=message.text).status_code == 400
    _request_id(_submit(client, employee_id=message.employee_id, text=message.text))


def test_live_mode_stops_at_its_spend_cap(tmp_path):
    client = _app(tmp_path, live=True, client=_client(), max_requests=1)
    message = MESSAGES["lr_001"]
    _request_id(_submit(client, employee_id=message.employee_id, text=message.text))
    capped = _submit(client, employee_id=message.employee_id, text=message.text)
    assert capped.status_code == 429 and "--max-requests" in capped.text
    assert sum(e["event"] == "leave_request_submitted" for e in _entries(tmp_path)) == 1


def test_a_request_that_stops_before_the_rules_says_to_submit_it_again(tmp_path):
    client = _app(tmp_path, live=True, client=fake_client(stop_reason="refusal"))
    message = MESSAGES["lr_001"]
    response = _submit(client, employee_id=message.employee_id, text=message.text)
    page = client.get(response.headers["location"]).text
    assert "stopped before the rules ran" in page and "submit it again" in page
    assert "nothing was kept" not in page  # the log keeps its keyed hash and redaction counts


def test_the_result_page_shows_only_requests_submitted_here(tmp_path):
    """It says where a request stands, so it answers only for the requests this app added."""
    client = _app(tmp_path)
    _request_id(_submit(client, message_id="lr_001"))
    assert client.get("/requests/lr_001").status_code == 404
    assert client.get("/requests/lr_web_00000000").status_code == 404


def test_the_result_page_shows_no_reasons_or_route(tmp_path):
    """Whoever submits sees the request's ID and state; HR sees the rest in the panel."""
    client = _app(tmp_path)
    response = _submit(client, message_id="lr_018")  # a parent-in-law: refused by a rule
    page = client.get(response.headers["location"]).text
    assert "waiting for HR" in page
    assert "parent" not in page.lower() and "reason" not in page.lower()


def test_pages_carry_the_security_headers_and_no_scripts(tmp_path):
    client = _app(tmp_path)
    response = client.get("/")
    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in response.text
    # "no-referrer" makes a browser post the form with "Origin: null", which the origin check
    # refuses; "same-origin" sends the origin to this app and nothing to anyone else.
    assert response.headers["referrer-policy"] == "same-origin"


def test_the_routes_are_the_form_the_submission_and_the_result(tmp_path):
    app = submit.create_app(
        live=False, audit_db=tmp_path / "audit.sqlite", registry_dir=TEST_REGISTRY_DIR
    )
    methods = {route.path: route.methods for route in app.routes}
    assert methods == {
        "/": {"GET", "HEAD"},
        "/panel.css": {"GET", "HEAD"},
        "/submit": {"POST"},
        "/requests/{request_id}": {"GET", "HEAD"},
    }


def test_the_form_says_synthetic_data_only_and_what_live_mode_costs(tmp_path):
    assert "Synthetic data only" in _app(tmp_path).get("/").text
    live = _app(tmp_path, live=True, client=_client()).get("/").text
    assert "Synthetic data only" in live and "calls the Claude API" in live
    assert "<textarea" in live


def test_the_cli_needs_a_mode_and_a_loopback_host(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        submit.main([])
    with pytest.raises(SystemExit):
        submit.main(["--demo", "--live"])
    assert submit.main(["--live", "--host", "0.0.0.0"]) == 2
    monkeypatch.setattr(submit, "DEMO_DIR", tmp_path / "no-demo")
    with pytest.raises(SystemExit, match="panel demo"):
        submit.main(["--demo"])
