"""HM-25: the system never replies to, contacts or sends anything to anyone.

Each test proves one point of the rule: one outbound data path (GET, one host, one API path),
refusals recorded; no send-capable tool can be declared, granted or served; drafts are kept and
marked never-sent with no field that could address them; and a static check, run in CI, fails
when a sending capability is added. No network: every transport here is a mock.
"""

import json
import textwrap
from pathlib import Path

import anthropic
import httpx
import httpx2
import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from hall_monitor import no_send
from hall_monitor.approvals import current_approvals
from hall_monitor.complaints.drafts import DraftResponse, store_draft
from hall_monitor.egress import (
    CFPB_API_HOST,
    CFPB_API_PATH,
    CFPB_POLICY,
    MODEL_API_POLICY,
    EgressRefused,
    audit_refusals,
    guarded_client,
    model_client,
)
from hall_monitor.governance.audit import AuditLog
from hall_monitor.governance.controls import ROOT
from hall_monitor.registry import AgentManifest, Registry, SendCapabilityError
from hall_monitor.tools.gateway import ToolGateway
from tests.fakes import TEST_REGISTRY_DIR

CFPB = f"https://{CFPB_API_HOST}{CFPB_API_PATH}"


def _mock(seen: list[httpx.Request], response=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response(request) if response else httpx.Response(200, json={"hits": {}})

    return httpx.MockTransport(handler)


# 1. One outbound data path: GET, the CFPB host, the complaint API path.


def test_a_get_to_the_cfpb_complaint_api_goes_through():
    seen: list[httpx.Request] = []
    with guarded_client(CFPB_POLICY, transport=_mock(seen)) as client:
        assert client.get(f"{CFPB}?size=1").status_code == 200
        assert client.get(f"{CFPB}32464970").status_code == 200
    assert [r.method for r in seen] == ["GET", "GET"]


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", CFPB),  # a write to the API itself
        ("PUT", CFPB),
        ("DELETE", CFPB),
        ("PATCH", CFPB),
        ("GET", "https://example.com/data-research/consumer-complaints/search/api/v1/"),
        ("GET", f"https://{CFPB_API_HOST}/complaint/"),  # CFPB's own complaint form
        ("GET", f"https://{CFPB_API_HOST}/data-research/consumer-complaints/"),
        ("GET", f"http://{CFPB_API_HOST}{CFPB_API_PATH}"),  # not TLS
        ("GET", f"https://{CFPB_API_HOST}:8443{CFPB_API_PATH}"),  # another port
        ("GET", f"https://{CFPB_API_HOST}@evil.example{CFPB_API_PATH}"),  # userinfo trick
        ("GET", f"https://{CFPB_API_HOST}.evil.example{CFPB_API_PATH}"),
        ("GET", f"https://{CFPB_API_HOST}{CFPB_API_PATH}../../complaint/"),  # path traversal
        ("GET", "https://hooks.slack.com/services/T000/B000/XXXX"),  # a webhook
    ],
)
def test_anything_else_is_refused_before_it_leaves_and_recorded(tmp_path, method, url):
    seen: list[httpx.Request] = []
    audit = AuditLog(tmp_path / "audit.sqlite")
    with guarded_client(CFPB_POLICY, transport=_mock(seen), on_refused=audit_refusals(audit)) as c:
        with pytest.raises(EgressRefused):
            c.request(method, url, content=b"hello" if method != "GET" else None)
    assert seen == [], "the request reached the transport"
    [entry] = audit.entries()
    assert entry["event"] == "egress_refused" and entry["actor"] == "pipeline"
    payload = entry["payload"]
    assert payload["method"] == method and payload["policy"] == "cfpb_complaint_api"
    assert len(payload["url_sha256"]) == 64
    assert "hello" not in json.dumps(payload)  # never the body
    assert audit.verify().ok


def test_a_redirect_is_never_followed():
    seen: list[httpx.Request] = []
    redirect = lambda r: httpx.Response(302, headers={"location": "https://evil.example/"})  # noqa: E731
    with guarded_client(CFPB_POLICY, transport=_mock(seen, redirect)) as client:
        assert client.get(CFPB).status_code == 302
        # Even asked to follow it, the next hop is checked again and refused.
        with pytest.raises(EgressRefused):
            client.get(CFPB, follow_redirects=True)
    assert [str(r.url) for r in seen] == [CFPB, CFPB]


def test_the_model_client_reaches_only_the_messages_endpoint(tmp_path):
    seen: list[httpx.Request] = []
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    audit = AuditLog(tmp_path / "audit.sqlite")
    client = model_client(
        api_key="test-key",
        transport=httpx2.MockTransport(
            lambda r: seen.append(r) or httpx2.Response(200, json=message)
        ),
        on_refused=audit_refusals(audit),
    )
    ask = {
        "model": "claude-haiku-5-5",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert client.messages.create(**ask).content[0].text == "ok"
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/v1/messages")]
    # Pointed anywhere else, the same client is refused before anything is sent.
    with pytest.raises(anthropic.APIConnectionError):
        client.with_options(base_url="https://collector.example").messages.create(**ask)
    assert len(seen) == 1
    assert [e["payload"]["policy"] for e in audit.entries()] == ["model_api"]


def test_the_policies_allow_one_method_each():
    assert {r.method for r in CFPB_POLICY.rules} == {"GET"}
    assert {r.method for r in MODEL_API_POLICY.rules} == {"POST"}


# 2. No send-capable tool can be declared, granted or served.


def _manifest(**changes) -> AgentManifest:
    fields = {
        "id": "complaint_probe",
        "name": "Probe",
        "version": "0.1.0",
        "owner": "own-301",
        "purpose": "test",
        "risk_tier": "low",
        "status": "active",
        "model": "claude-haiku-5-5",
    }
    return AgentManifest(**(fields | changes))


@pytest.mark.parametrize(
    "tool",
    [
        "send_email",
        "reply_to_consumer",
        "post_webhook",
        "notify_company",
        "submit_to_cfpb",
        "sms_customer",
        "ticket_update",
    ],  # the last isn't in the read-only catalogue
)
def test_a_manifest_asking_for_a_send_capable_tool_is_refused_at_run_time(tool):
    registry = Registry([_manifest(allowed_tools=("policy_lookup", tool))])
    with pytest.raises(SendCapabilityError, match=tool):
        registry.require("complaint_probe")
    with pytest.raises(SendCapabilityError):
        registry.check_tool("complaint_probe", "policy_lookup")


def test_a_manifest_with_read_only_tools_still_runs():
    registry = Registry([_manifest(allowed_tools=("policy_lookup", "claims_history"))])
    assert registry.require("complaint_probe").allowed_tools == ("policy_lookup", "claims_history")


def test_a_send_capable_manifest_cannot_be_approved():
    with pytest.raises(SendCapabilityError):
        current_approvals(previous={}, manifests=[_manifest(allowed_tools=("send_email",))])


def test_the_gateway_refuses_to_start_on_a_server_exposing_a_send_tool():
    server = FastMCP("probe")

    @server.tool(name="send_reply")
    def send_reply(text: str) -> dict:
        return {}

    with pytest.raises(SendCapabilityError, match="send_reply"):
        ToolGateway(Registry.from_dir(TEST_REGISTRY_DIR), server)


def test_every_tool_the_shipped_servers_expose_is_in_the_read_only_catalogue():
    gateway = ToolGateway(Registry.from_dir(TEST_REGISTRY_DIR))
    assert set(gateway._definitions) <= set(no_send.READ_ONLY_TOOLS)
    assert not [t for t in no_send.READ_ONLY_TOOLS if no_send.SEND_WORDS.search(t)]


# 3. Drafts are kept inside the system, marked never-sent, with no delivery path.


def test_a_draft_has_no_field_that_could_address_or_deliver_it():
    fields = set(DraftResponse.model_fields)
    assert fields == {"complaint_id", "text", "never_sent", "written_by", "prompt_version"}
    draft = DraftResponse(
        complaint_id="c1", text="Thank you.", written_by="complaint_triage", prompt_version="v1"
    )
    assert draft.never_sent is True
    with pytest.raises(ValidationError):
        DraftResponse(**draft.model_dump() | {"never_sent": False})
    for extra in ("recipient", "to", "email", "channel", "send_at", "webhook_url"):
        with pytest.raises(ValidationError):
            DraftResponse(**draft.model_dump(), **{extra: "x"})


def test_a_stored_draft_is_kept_by_hash_and_logged_as_never_sent(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite")
    draft = DraftResponse(
        complaint_id="c1",
        text="We are looking into it.",
        written_by="complaint_triage",
        prompt_version="v1",
    )
    digest = store_draft(audit, draft)
    assert audit.get_evidence(digest)["text"] == "We are looking into it."
    [entry] = audit.entries("c1")
    assert entry["event"] == "draft_stored"
    assert entry["payload"] == {
        "draft_sha256": digest,
        "never_sent": True,
        "delivery": "none",
        "written_by": "complaint_triage",
        "prompt_version": "v1",
    }


# 4. A static check, run in CI, fails when a sending capability is added.


def test_the_repo_has_no_sending_capability():
    assert no_send.scan(ROOT) == []


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("import smtplib\n", "banned_import"),
        ("from email.mime.text import MIMEText\n", "banned_import"),
        ("import requests\n", "banned_import"),
        ("import urllib.request\n", "banned_import"),
        ("from urllib import request\n", "banned_import"),
        ("import socket\n", "banned_import"),
        ("from http.client import HTTPSConnection\n", "banned_import"),
        ("import aiohttp\n", "banned_import"),
        ("from slack_sdk import WebClient\n", "banned_import"),
        ("import twilio.rest\n", "banned_import"),
        ("import boto3\n", "banned_import"),
        ("import webbrowser\n", "banned_import"),
        ("import httpx\n", "httpx_outside_egress"),
        ("from httpx2 import Client\n", "httpx_outside_egress"),
        ("x = __import__('smtplib')\n", "dynamic_import"),
        ("import importlib\nimportlib.import_module('requests')\n", "dynamic_import"),
        ("def f(c):\n    return c.post('https://example.com')\n", "send_method"),
        ("def f(c):\n    return c.request('POST', 'https://example.com')\n", "send_method"),
        ("def f(c):\n    return c.sendmail('a', 'b', 'c')\n", "send_method"),
        ("import subprocess\nsubprocess.run(['curl', 'x'])\n", "banned_import"),
        ("import os\nos.system('mail x')\n", "shell_command"),
    ],
)
def test_the_static_check_catches_each_kind_of_sending_capability(tmp_path, source, rule):
    package = tmp_path / "hall_monitor"
    package.mkdir()
    (package / "added.py").write_text(source)
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["pydantic>=2"]\n')
    findings = no_send.scan(tmp_path)
    assert [f.rule for f in findings] == [rule], findings
    assert findings[0].path == "hall_monitor/added.py"


@pytest.mark.parametrize("package", ["sendgrid>=6", "twilio", "slack-sdk==3.0", "Requests"])
def test_the_static_check_catches_a_sending_dependency(tmp_path, package):
    (tmp_path / "hall_monitor").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
        [project]
        dependencies = ["pydantic>=2", "{package}"]
        """)
    )
    assert [f.rule for f in no_send.scan(tmp_path)] == ["sending_dependency"]


def test_the_cli_exits_non_zero_on_a_finding(tmp_path, capsys):
    (tmp_path / "hall_monitor").mkdir()
    (tmp_path / "hall_monitor" / "x.py").write_text("import smtplib\n")
    (tmp_path / "pyproject.toml").write_text("[project]\ndependencies = []\n")
    assert no_send.main(["check", "--root", str(tmp_path)]) == 1
    assert "smtplib" in capsys.readouterr().out
    assert no_send.main(["check"]) == 0


def test_ci_runs_the_no_send_check():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "uv run python -m hall_monitor.no_send check" in ci


@pytest.mark.parametrize(
    "page", ["README.md", "docs/complaint-desk-design.md", "docs/adr/0021-no-reply-no-send.md"]
)
def test_the_docs_state_the_rule(page):
    text = " ".join(Path(ROOT / page).read_text().split())
    assert "never replies to, contacts or sends anything to anyone" in text
