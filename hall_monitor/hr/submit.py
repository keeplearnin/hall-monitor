"""A form that adds leave requests to HR's queue: its own app, so the control panel stays read-only.

    uv run python -m hall_monitor.panel demo            # once: builds var/demo/
    uv run python -m hall_monitor.hr.submit --demo      # pick a synthetic message; scripted, free
    uv run python -m hall_monitor.hr.submit --live      # write one; calls the Claude API

Each submission goes through the same pipeline as `python -m hall_monitor.hr.run`
(hr/pipeline.py): redaction, the check for text aimed at the AI, the leave agents, the rules in
code, and the audit log, which records IDs, keyed hashes, codes and costs, never the message.
The request then waits in the panel's review queue for HR to sign a decision
(`python -m hall_monitor.hr.review`). The form decides nothing and shows the submitter only the
request's ID and state.

Demo mode writes to the demo log (var/demo/) with the demo builder's scripted model, which
answers from the answer key, so it takes only the synthetic messages. Live mode takes free text
and calls the model for each one, so it stops at a spend cap (--max-requests, 20 by default).

There is no sign-in, so like the panel it answers only requests addressed to this machine. A
page elsewhere could still make the browser post the form here, so a submission must carry the
form's token, which no other site can read, and come from this app's own origin.
"""

from __future__ import annotations

import argparse
import hmac
import secrets
import sys
import threading
from datetime import datetime
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from hall_monitor.agents.base import default_client
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog
from hall_monitor.hr import signoff
from hall_monitor.hr.data import load_company, load_employees, load_messages
from hall_monitor.hr.models import LeaveMessage
from hall_monitor.hr.pipeline import prepare
from hall_monitor.panel import demo
from hall_monitor.panel.app import LOCAL_HOSTS, SECURITY_HEADERS
from hall_monitor.panel.pages import CSP, Safe, esc, tag
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, Registry
from hall_monitor.spend import DEFAULT_SPEND_DB, SpendLedger

DEFAULT_PORT = 8766  # the panel is on 8765
DEMO_DIR = demo.DEFAULT_OUT
MAX_TEXT = LeaveMessage.model_fields["text"].metadata[-1].max_length
# Room for the longest message the form allows: URL-encoding makes a character up to 9 bytes.
MAX_BODY = 9 * MAX_TEXT + 1024
# The panel's headers, except that a browser must send this app its origin when it posts the
# form: under "no-referrer" it sends "Origin: null", which the origin check refuses.
HEADERS = SECURITY_HEADERS | {"Referrer-Policy": "same-origin"}
STATES = {
    "waiting for HR": "waiting for HR",
    "stopped before the rules ran; can be prepared again": (
        "stopped before the rules ran (the intake agent's call failed or it is stopped); the "
        "message itself wasn't kept, so submit it again once that is fixed"
    ),
    "decided": "decided by HR",
}


class _Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _page(title: str, body: Safe, *, live: bool) -> str:
    mode = (
        tag(
            "p",
            tag("strong", "Live. "),
            "Each submission calls the Claude API (about $0.003) and goes into the audit log "
            "HR works from.",
            class_="banner alert",
        )
        if live
        else tag(
            "p",
            tag("strong", "Demo. "),
            "A scripted model answers from the answer key, into the demo audit log; nothing "
            "calls the Claude API.",
            class_="banner demo",
        )
    )
    doc = tag(
        "html",
        tag(
            "head",
            Safe('<meta charset="utf-8">'),
            Safe('<meta name="viewport" content="width=device-width, initial-scale=1">'),
            Safe(f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'),
            tag("title", f"{title} · hall-monitor leave requests"),
            Safe('<link rel="stylesheet" href="/panel.css">'),
        ),
        tag(
            "body",
            tag(
                "header",
                tag(
                    "div",
                    tag("a", "hall-monitor", href="/", class_="brand"),
                    tag("span", "submit a leave request", class_="brand-sub"),
                    class_="brand-row",
                ),
            ),
            tag(
                "main",
                tag("h1", title),
                tag(
                    "p",
                    tag("strong", "Synthetic data only. "),
                    "Don't type real names, conditions or identifiers. The form adds a request "
                    "to HR's queue and decides nothing; HR signs every decision.",
                    class_="banner",
                ),
                mode,
                body,
            ),
        ),
        lang="en",
    )
    return "<!doctype html>" + doc


def create_app(
    *,
    live: bool,
    audit_db: Path,
    registry_dir: Path = DEFAULT_REGISTRY_DIR,
    client=None,
    max_requests: int = 20,
    spend: SpendLedger | None = None,
) -> Starlette:
    """The form. Live mode calls `client` (the Claude client); demo mode uses the scripted one."""
    if not live:
        client = SimpleNamespace(messages=demo._ScriptedLeaveMessages())
    company, employees = load_company(), load_employees()
    messages = {m.id: m for m in load_messages()}
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    submitted: set[str] = set()  # the requests this app added: the only ones it reports on
    spent = 0

    def html(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers=HEADERS)

    def form(error: str | None = None, text: str = "", status: int = 200) -> HTMLResponse:
        fields: list[object] = [Safe(f'<input type="hidden" name="token" value="{token}">')]
        if live:
            fields += [
                tag("label", "Employee", for_="employee_id"),
                tag(
                    "select",
                    *(tag("option", e, value=e) for e in sorted(employees)),
                    id="employee_id",
                    name_="employee_id",
                    required="required",
                ),
                tag("label", "The request, as the employee would write it", for_="text"),
                tag(
                    "textarea",
                    text,
                    id="text",
                    name_="text",
                    rows="8",
                    maxlength=str(MAX_TEXT),
                    required="required",
                ),
            ]
        else:
            fields += [
                tag("label", "Synthetic message", for_="message_id"),
                tag(
                    "select",
                    *(
                        tag("option", f"{m.id} · {m.employee_id} · {m.text[:90]}", value=m.id)
                        for m in messages.values()
                    ),
                    id="message_id",
                    name_="message_id",
                    required="required",
                ),
            ]
        fields.append(tag("button", "Add to HR's queue", type="submit"))
        body = [
            tag("p", error, class_="banner alert", role="alert") if error else "",
            tag(
                "form",
                *fields,
                method="post",
                action="/submit",
                accept_charset="utf-8",
                class_="card submit-form",
            ),
        ]
        return html(_page("Add a leave request", Safe("".join(map(esc, body))), live=live), status)

    async def read_form(request: Request) -> dict[str, str]:
        """The posted fields, from this app's own form only."""
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if site not in (None, "same-origin") or origin != f"http://{request.headers['host']}":
            raise _Refused(403, "This form only takes submissions from its own page.")
        if not request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            raise _Refused(415, "Submit the form on the page.")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY:
            raise _Refused(413, "That submission is too large.")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                raise _Refused(413, "That submission is too large.")
        try:
            parsed = parse_qs(
                body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=8
            )
        except (UnicodeDecodeError, ValueError):
            raise _Refused(400, "Submit the form on the page.") from None
        if any(len(v) != 1 for v in parsed.values()):
            raise _Refused(400, "Submit the form on the page.")
        fields = {k: v[0] for k, v in parsed.items()}
        if not hmac.compare_digest(fields.pop("token", ""), token):
            raise _Refused(403, "This form only takes submissions from its own page.")
        return fields

    def message_from(fields: dict[str, str]) -> LeaveMessage:
        expected = {"employee_id", "text"} if live else {"message_id"}
        if set(fields) != expected:
            raise _Refused(400, "Fill in the form as it is.")
        if not live:
            chosen = messages.get(fields["message_id"])
            if chosen is None:
                raise _Refused(400, "Pick one of the synthetic messages.")
            employee_id, text = chosen.employee_id, chosen.text
        else:
            # A browser sends a textarea's line breaks as CRLF; its maxlength counts each as one.
            employee_id, text = fields["employee_id"], fields["text"].replace("\r\n", "\n")
            if employee_id not in employees:
                raise _Refused(400, "Pick one of the synthetic employees.")
            if not text.strip():
                raise _Refused(400, "Write the request.")
            if len(text) > MAX_TEXT:
                raise _Refused(400, f"A request is at most {MAX_TEXT:,} characters.")
        try:
            return LeaveMessage(
                id=f"lr_web_{secrets.token_hex(4)}",
                employee_id=employee_id,
                submitted_at=datetime.now().astimezone(),
                text=text,
            )
        except ValidationError:
            raise _Refused(400, "Write the request as plain text.") from None

    def run(message: LeaveMessage) -> None:
        audit = AuditLog(audit_db)
        try:
            if audit.entries(message.id):  # an ID already used: vanishingly rare
                raise _Refused(409, "Something went wrong; submit it again.")
            prepare(
                message,
                employees=employees,
                company=company,
                registry=Registry.from_dir(registry_dir),
                client=client,
                audit=audit,
                # Live submissions are held to the leave caps (HM-34).
                spend=spend if live else None,
            )
        finally:
            audit.close()

    async def home(request: Request) -> Response:
        return form()

    async def stylesheet(request: Request) -> Response:
        css = resources.files("hall_monitor.panel").joinpath("panel.css").read_text()
        return Response(css, media_type="text/css", headers=HEADERS)

    async def add(request: Request) -> Response:
        nonlocal spent
        text = ""
        try:
            fields = await read_form(request)
            text = fields.get("text", "")[:MAX_TEXT] if live else ""
            message = message_from(fields)
            with lock:
                if live and spent >= max_requests:
                    raise _Refused(
                        429,
                        f"This run has made its {max_requests} live submissions; restart it "
                        "with --max-requests to allow more.",
                    )
                spent += 1
            await run_in_threadpool(run, message)
        except _Refused as refused:
            if refused.status in (403, 413, 415):
                return html(
                    _page("Not accepted", tag("p", str(refused)), live=live), refused.status
                )
            return form(str(refused), text, refused.status)
        with lock:
            submitted.add(message.id)
        return RedirectResponse(f"/requests/{quote(message.id)}", status_code=303)

    async def result(request: Request) -> Response:
        request_id = request.path_params["request_id"]
        with lock:
            known = request_id in submitted
        if not known:
            return html(
                _page("Not found", tag("p", "No request with that ID was added here."), live=live),
                404,
            )
        audit = AuditLog(audit_db, read_only=True)
        try:
            state = signoff.status(audit, request_id)["state"]
        finally:
            audit.close()
        body = [
            tag(
                "dl",
                tag("div", tag("dt", "Request"), tag("dd", tag("code", request_id))),
                tag("div", tag("dt", "State"), tag("dd", STATES.get(state, state))),
                class_="facts",
            ),
            tag(
                "p",
                "HR sees it in the control panel's review queue and decides it with ",
                tag("code", "python -m hall_monitor.hr.review decide"),
                ".",
            ),
            tag("p", tag("a", "Add another request", href="/")),
        ]
        return html(_page("Request added", Safe("".join(map(esc, body))), live=live))

    return Starlette(
        routes=[
            Route("/", home),
            Route("/panel.css", stylesheet),
            Route("/submit", add, methods=["POST"]),
            Route("/requests/{request_id}", result),
        ],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.hr.submit")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="synthetic messages, scripted model")
    mode.add_argument("--live", action="store_true", help="free text; calls the Claude API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB, help="live mode")
    parser.add_argument("--max-requests", type=int, default=20, help="live submissions per run")
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost"):
        print("the form has no sign-in, so it serves only this machine", file=sys.stderr)
        return 2
    if args.demo:
        if not (DEMO_DIR / "audit.sqlite").exists():
            raise SystemExit("no demo data: run `python -m hall_monitor.panel demo` first")
        app = create_app(
            live=False, audit_db=DEMO_DIR / "audit.sqlite", registry_dir=DEMO_DIR / "registry"
        )
    else:
        from dotenv import load_dotenv

        load_dotenv()
        app = create_app(
            live=True,
            audit_db=args.audit_db,
            client=default_client(),
            max_requests=args.max_requests,
            spend=SpendLedger(DEFAULT_SPEND_DB),
        )
    import uvicorn

    print(f"Leave request form: http://{args.host}:{args.port}/", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
