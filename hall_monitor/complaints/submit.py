"""A form that adds a typed-in complaint to the complaint desk: its own app, so the panel stays
read-only (ADR 0029, HM-33).

    uv run python -m hall_monitor.panel demo                   # once: builds var/demo/
    uv run python -m hall_monitor.complaints.submit --demo     # scripted stand-in; free
    uv run python -m hall_monitor.complaints.submit --live     # calls the Claude API

Each submission becomes a Complaint built from the form's fields (CFPB's product, an issue and
sub-issue, its two tags, a narrative) with a synthetic ID ("syn-" and 12 hex digits; CFPB's IDs
are digits only, so the two can't collide). It then runs through the desk's own pipeline,
complaints/pipeline.py triage_complaint, agentic as complaints.run runs it: redaction, the check
for text aimed at the AI, the desk supervisor and triage agent, the routing rule in code, the
draft kept and never sent (HM-25), and the audit log, which records IDs, keyed hashes, codes and
costs, never the text. Every complaint ends with a person; the code picks the queue. The result
shows on the control panel's Complaint desk page. The form decides nothing and shows the
submitter only the complaint's ID and state, never the route, reasons or anything an agent wrote.

Demo mode writes to the demo log (var/demo/) with the scripted stand-in (complaints/scripted.py),
which answers from the labels and is marked scripted in the log. Live mode calls the Claude API
through the desk's egress-guarded client, which can reach only the messages endpoint, and stops
at a spend cap (--max-complaints, 5 by default).

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
import unicodedata
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from hall_monitor.complaints.ingest import PINNED_NAME, load_pinned
from hall_monitor.complaints.models import Complaint, Tag
from hall_monitor.complaints.pipeline import (
    RECEIVED,
    ROUTED,
    AlreadyTriaged,
    DeskLimits,
    scope_id,
    triage_complaint,
)
from hall_monitor.complaints.scripted import scripted_client
from hall_monitor.egress import audit_refusals, model_client
from hall_monitor.gateway.redaction import redact_text
from hall_monitor.governance.audit import DEFAULT_AUDIT_DB, AuditLog
from hall_monitor.panel import demo
from hall_monitor.panel.app import LOCAL_HOSTS, SECURITY_HEADERS
from hall_monitor.panel.pages import CSP, Safe, esc, tag
from hall_monitor.registry import DEFAULT_REGISTRY_DIR, Registry

DEFAULT_PORT = 8767  # the panel is on 8765, the leave form on 8766
DEMO_DIR = demo.DEFAULT_OUT
MAX_LABEL = 300  # CFPB's fixed-list labels (complaints/models.py Label)
MAX_NARRATIVE = 10_000
MAX_LIVE = 50  # the most --max-complaints allows
# Room for the longest submission the form allows: URL-encoding makes a character up to 9 bytes.
MAX_BODY = 9 * (MAX_NARRATIVE + 2 * MAX_LABEL + 100) + 1024
HEADERS = SECURITY_HEADERS | {"Referrer-Policy": "same-origin"}  # see hr/submit.py
PER_COMPLAINT_BUDGET = DeskLimits().max_cost_usd
TAGS = {f"tag_{t.value}": t for t in Tag}
TAG_LABELS = {Tag.OLDER_AMERICAN: "Older American", Tag.SERVICEMEMBER: "Servicemember"}
FIELDS = {"product", "issue", "sub_issue", "narrative"}

# Letters that display as blank (the Hangul fillers).
_BLANK_LETTERS = frozenset("\u115f\u1160\u3164\uffa0")


def _invisible(text: str, allowed: str = "") -> bool:
    """Control and format characters (C0 and C1 controls, bidi overrides, zero-width marks),
    which could hide text from a person reading the log's subject or the agent's input."""
    return any(
        (unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") or ch in _BLANK_LETTERS)
        and ch not in allowed
        for ch in text
    )


def _has_identifier(label: str) -> bool:
    """True if redaction would change the label: CFPB's labels are a fixed list and aren't
    redacted, so the form's typed ones must hold nothing redaction removes."""
    return redact_text(label) != label


STATES = {
    "routed": "routed to a person at the complaint desk",
    "received": (
        "received, and its triage didn't finish, so it isn't routed yet; the desk's people "
        "see it on the panel as claimed, not routed"
    ),
}


class _Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def live_client(audit: AuditLog):
    """The desk's Claude client (HM-25): it reaches only the messages endpoint, with every
    refusal written to this audit log, as complaints.run builds it."""
    return model_client(on_refused=audit_refusals(audit))


def _page(title: str, body: Safe, *, live: bool) -> str:
    mode = (
        tag(
            "p",
            tag("strong", "Live. "),
            "Each submission calls the Claude API: the desk supervisor and the triage agent "
            "(Haiku), typically well under a cent; the desk makes no further model call once a "
            f"complaint has cost {PER_COMPLAINT_BUDGET} USD (a call already started can take "
            "it a little past that). It goes into the audit log the desk works from.",
            class_="banner alert",
        )
        if live
        else tag(
            "p",
            tag("strong", "Demo. "),
            "A scripted stand-in answers from the complaint's labels, into the demo audit log; "
            "nothing calls the Claude API.",
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
            tag("title", f"{title} · hall-monitor complaint desk"),
            Safe('<link rel="stylesheet" href="/panel.css">'),
        ),
        tag(
            "body",
            tag(
                "header",
                tag(
                    "div",
                    tag("a", "hall-monitor", href="/", class_="brand"),
                    tag("span", "add a complaint to the desk", class_="brand-sub"),
                    class_="brand-row",
                ),
            ),
            tag(
                "main",
                tag("h1", title),
                tag(
                    "p",
                    tag("strong", "Synthetic data only. "),
                    "Don't type a real person's complaint, name, account or other details. The "
                    "form adds a complaint to the desk and decides nothing: code picks the "
                    "queue, a person works every complaint, and nothing is sent to anyone.",
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
    max_complaints: int = 5,
) -> Starlette:
    """The form. Demo mode uses the scripted stand-in; live mode uses `client` if given (tests
    pass a fake) or else builds the desk's guarded client for each submission."""
    pinned = load_pinned(PINNED_NAME)
    products = sorted({c.product for c in pinned})
    issues = sorted({c.issue for c in pinned})
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    submitted: set[str] = set()  # the complaints this app added: the only ones it reports on
    spent = 0

    def html(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers=HEADERS)

    def form(
        error: str | None = None, values: dict[str, str] | None = None, status: int = 200
    ) -> HTMLResponse:
        values = values or {}
        chosen = values.get("product", "")
        fields: list[object] = [
            Safe(f'<input type="hidden" name="token" value="{token}">'),
            tag("label", "Product (CFPB's list)", for_="product"),
            tag(
                "select",
                *(
                    tag("option", p, value=p, selected="selected" if p == chosen else None)
                    for p in products
                ),
                id="product",
                name_="product",
                required="required",
            ),
            tag("label", "Issue", for_="issue"),
            Safe(
                f'<input type="text" id="issue" name="issue" list="issues" required '
                f'maxlength="{MAX_LABEL}" value="{esc(values.get("issue", ""))}">'
            ),
            tag("datalist", *(tag("option", value=i) for i in issues), id="issues"),
            tag("label", "Sub-issue (optional)", for_="sub_issue"),
            Safe(
                f'<input type="text" id="sub_issue" name="sub_issue" maxlength="{MAX_LABEL}" '
                f'value="{esc(values.get("sub_issue", ""))}">'
            ),
            tag("span", "Tags (optional)", class_="label"),
            *(
                tag(
                    "label",
                    Safe(
                        f'<input type="checkbox" name="{name}" value="on"'
                        f"{' checked' if values.get(name) == 'on' else ''}> "
                    ),
                    TAG_LABELS[t],
                )
                for name, t in TAGS.items()
            ),
            tag("label", "What happened, as the consumer would write it (optional)", for_="n"),
            tag(
                "textarea",
                values.get("narrative", ""),
                id="n",
                name_="narrative",
                rows="8",
                maxlength=str(MAX_NARRATIVE),
            ),
            tag("button", "Add to the complaint desk", type="submit"),
        ]
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
        return html(_page("Add a complaint", Safe("".join(map(esc, body))), live=live), status)

    async def read_form(request: Request) -> dict[str, str]:
        """The posted fields, from this app's own form only (as hr/submit.py)."""
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
                body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=10
            )
        except (UnicodeDecodeError, ValueError):
            raise _Refused(400, "Submit the form on the page.") from None
        if any(len(v) != 1 for v in parsed.values()):
            raise _Refused(400, "Submit the form on the page.")
        fields = {k: v[0] for k, v in parsed.items()}
        if not hmac.compare_digest(fields.pop("token", "").encode(), token.encode()):
            raise _Refused(403, "This form only takes submissions from its own page.")
        return fields

    def complaint_from(fields: dict[str, str]) -> Complaint:
        if not FIELDS <= set(fields) <= FIELDS | set(TAGS):
            raise _Refused(400, "Fill in the form as it is.")
        if any(fields[name] != "on" for name in TAGS if name in fields):
            raise _Refused(400, "Fill in the form as it is.")
        product = fields["product"]
        if product not in products:
            raise _Refused(400, "Pick one of CFPB's products.")
        issue, sub_issue = fields["issue"].strip(), fields["sub_issue"].strip()
        # A browser sends a textarea's line breaks as CRLF; its maxlength counts each as one.
        narrative = fields["narrative"].replace("\r\n", "\n")
        if not issue:
            raise _Refused(400, "Write the issue.")
        if len(issue) > MAX_LABEL or len(sub_issue) > MAX_LABEL:
            raise _Refused(400, f"An issue or sub-issue is at most {MAX_LABEL} characters.")
        if len(narrative) > MAX_NARRATIVE:
            raise _Refused(400, f"What happened is at most {MAX_NARRATIVE:,} characters.")
        if _invisible(issue + sub_issue) or _invisible(narrative, allowed="\n\t"):
            raise _Refused(400, "Write the complaint as plain text.")
        if _has_identifier(issue) or _has_identifier(sub_issue):
            raise _Refused(
                400,
                "Keep the issue and sub-issue to a short label, with no email address, phone "
                "number or other identifier; details go in the narrative, which is redacted.",
            )
        return Complaint(
            complaint_id=f"syn-{secrets.token_hex(6)}",
            date_received=datetime.now(UTC),
            date_sent_to_company=None,
            product=product,
            sub_product=None,
            issue=issue,
            sub_issue=sub_issue or None,
            state=None,
            zip3=None,
            tags=tuple(sorted(t for name, t in TAGS.items() if name in fields)),
            submitted_via=None,
            company_response=None,
            company_public_response=None,
            timely=None,
            narrative=narrative if narrative.strip() else None,
        )

    def run(complaint: Complaint) -> None:
        audit = AuditLog(audit_db)
        try:
            if not live:
                desk_client = scripted_client()
            else:
                desk_client = client if client is not None else live_client(audit)
            try:
                triage_complaint(
                    complaint,
                    registry=Registry.from_dir(registry_dir),
                    client=desk_client,
                    audit=audit,
                )
            except AlreadyTriaged:  # an ID already used: vanishingly rare
                raise _Refused(409, "Something went wrong; submit it again.") from None
            except Exception:
                # The pipeline routes a claimed complaint to a person before it re-raises;
                # one that never got that far was never added.
                if not audit.has(scope_id(complaint.complaint_id), RECEIVED):
                    raise _Refused(
                        500, "Something went wrong before the desk received it; nothing was added."
                    ) from None
        finally:
            audit.close()

    async def home(request: Request) -> Response:
        return form()

    async def stylesheet(request: Request) -> Response:
        css = resources.files("hall_monitor.panel").joinpath("panel.css").read_text()
        return Response(css, media_type="text/css", headers=HEADERS)

    async def add(request: Request) -> Response:
        nonlocal spent
        values: dict[str, str] = {}
        try:
            fields = await read_form(request)
            values = {k: v[:MAX_NARRATIVE] for k, v in fields.items()}
            complaint = complaint_from(fields)
            with lock:
                if live and spent >= max_complaints:
                    raise _Refused(
                        429,
                        f"This run has made its {max_complaints} live submissions; restart it "
                        "with --max-complaints to allow more.",
                    )
                spent += 1
            await run_in_threadpool(run, complaint)
        except _Refused as refused:
            if refused.status in (403, 413, 415):
                return html(
                    _page("Not accepted", tag("p", str(refused)), live=live), refused.status
                )
            return form(str(refused), values, refused.status)
        with lock:
            submitted.add(complaint.complaint_id)
        return RedirectResponse(f"/complaints/{quote(complaint.complaint_id)}", status_code=303)

    async def result(request: Request) -> Response:
        complaint_id = request.path_params["complaint_id"]
        with lock:
            known = complaint_id in submitted
        if not known:
            return html(
                _page(
                    "Not found", tag("p", "No complaint with that ID was added here."), live=live
                ),
                404,
            )
        audit = AuditLog(audit_db, read_only=True)
        try:
            scope = scope_id(complaint_id)
            state = "routed" if audit.has(scope, ROUTED) else "received"
        finally:
            audit.close()
        body = [
            tag(
                "dl",
                tag("div", tag("dt", "Complaint"), tag("dd", tag("code", complaint_id))),
                tag("div", tag("dt", "State"), tag("dd", STATES[state])),
                class_="facts",
            ),
            tag(
                "p",
                "The desk's people see it on the control panel's Complaint desk page, as ",
                tag("code", scope),
                ". Nothing is sent to anyone.",
            ),
            tag("p", tag("a", "Add another complaint", href="/")),
        ]
        return html(_page("Complaint added", Safe("".join(map(esc, body))), live=live))

    return Starlette(
        routes=[
            Route("/", home),
            Route("/panel.css", stylesheet),
            Route("/submit", add, methods=["POST"]),
            Route("/complaints/{complaint_id}", result),
        ],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hall_monitor.complaints.submit")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="scripted stand-in, no API calls")
    mode.add_argument("--live", action="store_true", help="calls the Claude API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB, help="live mode")
    parser.add_argument(
        "--max-complaints", type=int, default=5, help=f"live submissions per run (1-{MAX_LIVE})"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.max_complaints <= MAX_LIVE:
        parser.error(f"--max-complaints must be between 1 and {MAX_LIVE}")
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
        app = create_app(live=True, audit_db=args.audit_db, max_complaints=args.max_complaints)
        print(
            f"Live: each complaint calls the Claude API (Haiku), typically well under a cent; "
            f"the desk makes no further model call once one has cost {PER_COMPLAINT_BUDGET} "
            f"USD, so with at most {args.max_complaints} complaints this run the total stays "
            f"near {args.max_complaints * PER_COMPLAINT_BUDGET:.2f} USD at most (each "
            "complaint's last call can take it slightly past its budget).",
            file=sys.stderr,
        )
    import uvicorn

    print(f"Complaint form: http://{args.host}:{args.port}/", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
