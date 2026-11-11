"""The control panel's read API and pages, as one Starlette app.

Every route is GET (HEAD comes with it): the panel has no way to change an agent, a stop, an
approval or a decision, and a test fails if a route that could is added. Pages and their JSON
share paths (`agents.html`, `api/agents.json`), so a saved snapshot works like the server.

There is no sign-in yet, so the app answers only requests addressed to this machine
(localhost), which also stops a web page elsewhere reaching it through DNS rebinding.
"""

from __future__ import annotations

from importlib import resources

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from hall_monitor.governance.audit import ChainHead
from hall_monitor.panel import desk, pages, views
from hall_monitor.panel.pages import CSP

# Names a browser on this machine uses for it. (The snapshot's client sends "localhost".)
LOCAL_HOSTS = ["localhost", "127.0.0.1"]

SECURITY_HEADERS = {
    # Only the panel's own stylesheet; no scripts, frames or other origins.
    "Content-Security-Policy": CSP + "; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _anchor(request: Request) -> ChainHead | None | str:
    """An anchored head from ?seq=&hash=, None when not given, or a message when malformed."""
    seq, digest = request.query_params.get("seq"), request.query_params.get("hash")
    if not seq and not digest:
        return None
    digest = (digest or "").strip().lower()
    seq = (seq or "").strip()
    if (
        not (seq.isascii() and seq.isdigit())
        or len(digest) != 64
        or not all(ch in "0123456789abcdef" for ch in digest)
    ):
        return "An anchored head is an entry number and its 64-character hex hash."
    return ChainHead(int(seq), digest)


def create_app(src: views.Sources | None = None) -> Starlette:
    src = src or views.Sources()

    def html(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers=SECURITY_HEADERS)

    def as_json(data, status: int = 200) -> JSONResponse:
        return JSONResponse(data, status_code=status, headers=SECURITY_HEADERS)

    def missing(what: str, *, root: str) -> HTMLResponse:
        return html(pages.not_found_page(what, root=root, demo=src.demo), 404)

    async def home(request: Request) -> Response:
        return RedirectResponse("index.html")

    async def stylesheet(request: Request) -> Response:
        css = resources.files("hall_monitor.panel").joinpath("panel.css").read_text()
        return Response(css, media_type="text/css", headers=SECURITY_HEADERS)

    async def overview(request: Request) -> Response:
        return html(pages.overview_page(views.overview(src)))

    async def agents(request: Request) -> Response:
        return html(pages.agents_page(views.agents(src), demo=src.demo))

    async def agent(request: Request) -> Response:
        found = views.agent(src, request.path_params["agent_id"])
        if found is None:
            return missing("agent", root="../")
        return html(pages.agent_page(found, demo=src.demo))

    async def queue(request: Request) -> Response:
        return html(
            pages.queue_page(views.review_queue(src), views.leave_queue(src), demo=src.demo)
        )

    async def cases(request: Request) -> Response:
        return html(pages.cases_page(views.cases(src), views.leave_requests(src), demo=src.demo))

    async def case(request: Request) -> Response:
        trace = views.case_trace(src, request.path_params["claim_id"])
        if trace is None:
            return missing("case", root="../")
        return html(pages.case_page(trace, demo=src.demo))

    async def audit(request: Request) -> Response:
        anchor = _anchor(request)
        if isinstance(anchor, str):
            return html(pages.page("Audit integrity", "audit.html", pages.tag("p", anchor)), 400)
        return html(pages.audit_page(views.audit_integrity(src, anchor), demo=src.demo))

    async def evals(request: Request) -> Response:
        return html(pages.evals_page(views.evals(src), demo=src.demo))

    async def complaints(request: Request) -> Response:
        return html(desk.complaints_page(desk.complaint_desk(src), demo=src.demo))

    async def controls(request: Request) -> Response:
        return html(pages.controls_page(views.controls(src), demo=src.demo))

    async def api(request: Request) -> Response:
        name = request.path_params["name"]
        readers = {
            "overview": views.overview,
            "agents": views.agents,
            "queue": views.review_queue,
            "cases": views.cases,
            "leave_queue": views.leave_queue,
            "leave_requests": views.leave_requests,
            "evals": views.evals,
            "controls": views.controls,
            "complaints": desk.complaint_desk,
        }
        if name == "audit":
            anchor = _anchor(request)
            if isinstance(anchor, str):
                return as_json({"error": anchor}, 400)
            return as_json(views.audit_integrity(src, anchor))
        if name not in readers:
            return as_json({"error": "not found"}, 404)
        return as_json(readers[name](src))

    async def api_agent(request: Request) -> Response:
        found = views.agent(src, request.path_params["agent_id"])
        return as_json(found) if found is not None else as_json({"error": "not found"}, 404)

    async def api_case(request: Request) -> Response:
        trace = views.case_trace(src, request.path_params["claim_id"])
        return as_json(trace) if trace is not None else as_json({"error": "not found"}, 404)

    routes = [
        Route("/", home),
        Route("/panel.css", stylesheet),
        Route("/index.html", overview),
        Route("/agents.html", agents),
        Route("/agents/{agent_id}.html", agent),
        Route("/queue.html", queue),
        Route("/cases.html", cases),
        Route("/cases/{claim_id}.html", case),
        Route("/audit.html", audit),
        Route("/evals.html", evals),
        Route("/controls.html", controls),
        Route("/complaints.html", complaints),
        Route("/api/agents/{agent_id}.json", api_agent),
        Route("/api/cases/{claim_id}.json", api_case),
        Route("/api/{name}.json", api),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)],
    )
