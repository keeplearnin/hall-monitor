"""The thin HTTP boundary: MCP tool calls over HTTP, into the same ToolGateway (RI-26, RI-29).

One endpoint, POST /mcp, speaking the MCP methods a tool client needs (initialize,
notifications/initialized, tools/list, tools/call) as single JSON-RPC requests with JSON
responses. Each request is checked here, in this order, and the first failure refuses it:

1. The Host is loopback (the app serves this machine only), and an Origin, if sent, is the
   boundary's own (MCP's transport rule against DNS rebinding).
2. POST, Content-Type application/json, a body of at most MAX_BODY_BYTES.
3. Exactly one Authorization header, "Bearer <token>"; the token passes tokens.verify (issuer,
   signature, audience, tenant, lifetime, claims) and its jti hasn't been used.
4. The body is one JSON-RPC 2.0 request object (a batch array is refused), its method is one
   of the four, and the Mcp-Method header names that same method: a proxy or a log that reads
   the header can't be told one thing while the server does another (RI-26).
5. The token's subject is a registered agent, never the pipeline's own actor.
6. tools/call: the tool is in the token's scope; then the ToolGateway decides as it does for
   any agent, with the token's policy as the case's scope: allow-list, data classes, redaction,
   the injection screen, the scope check, the grant rule for a crew, and the call's record.

Every request, refused or not, goes to the audit log as codes: the outcome, the refusal code,
the method and tool only when they are known names, the agent only when registered, and a keyed
hash of the token's jti; never the token, the arguments or the result. A tool call's own record
is the gateway's (EventRecorder), under the token's case.

A refusal before the token is accepted answers 401 with WWW-Authenticate naming only the code;
a tool outside the token's scope 403 (insufficient_scope); anything else a JSON-RPC error. The
gateway's own refusals come back as a tool result marked isError, as an in-process agent gets
them.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from hall_monitor.boundary.tokens import Claims, TokenRefused, verify
from hall_monitor.governance.audit import AuditLog, EventRecorder
from hall_monitor.registry import RegistryError
from hall_monitor.tools.gateway import PIPELINE_ACTOR, ToolGateway

LOCAL_HOSTS = ["127.0.0.1", "localhost"]
MAX_BODY_BYTES = 64 * 1024
METHODS = ("initialize", "notifications/initialized", "tools/list", "tools/call")
PROTOCOL_VERSION = "2025-06-18"
BOUNDARY_SCOPE = "boundary"  # the audit scope of a request refused before its token was read
SECURITY_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
# Headers the checks read: each may appear once (Authorization is counted with them).
CHECKED_HEADERS = ("host", "origin", "content-type", "content-length", "mcp-method")


class Refused(Exception):
    def __init__(self, status: int, code: str, rpc: int | None = None):
        super().__init__(code)
        self.status, self.code, self.rpc = status, code, rpc


class ReplayGuard:
    """Each jti once while its token is live (in memory: a restart forgets, and tokens live five
    minutes at most, so a replay after a restart is the gap)."""

    def __init__(self):
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def first_use(self, jti: str, exp: int, now: int) -> bool:
        with self._lock:
            for old in [j for j, e in self._seen.items() if e < now - 60]:
                del self._seen[old]
            if jti in self._seen:
                return False
            self._seen[jti] = exp
            return True


def create_app(
    gateway: ToolGateway,
    *,
    audience: str,
    tenant: str,
    issuers: dict[str, Ed25519PublicKey],
    audit: AuditLog,
    clock: Callable[[], float] = time.time,
) -> Starlette:
    """audience is the endpoint's own URL (http://127.0.0.1:<port>/mcp), which every token must
    name; issuers maps an issuer name to the public key its tokens are signed with."""
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(audience))
    replay = ReplayGuard()
    known_tools = set(gateway._definitions)
    registered = {m.id for m in gateway.registry}

    def record(claims: Claims | None, payload: dict) -> None:
        scope = claims.case if claims is not None else BOUNDARY_SCOPE
        audit.append(scope, "boundary_request", "boundary", payload)

    async def mcp(request: Request) -> Response:
        # Filled in as each part is known, so a refusal after the token was read is recorded
        # under its case with its jti's keyed hash, as an answered request is.
        seen: dict = {"claims": None}
        entry: dict = {"outcome": "refused", "code": None, "method": None, "tool": None}
        try:
            claims, body = await _admit(request, seen, entry)
            response = await _dispatch(claims, body, entry)
            entry["outcome"] = entry.pop("outcome_set", None) or "answered"
            record(claims, entry)
            return response
        except Refused as refused:
            entry["code"] = refused.code
            entry.pop("outcome_set", None)
            record(seen["claims"], entry)
            return _refusal(refused, body_id=None)

    async def _admit(request: Request, seen: dict, entry: dict) -> tuple[Claims, dict]:
        # Every header the checks read, once: with two, a proxy reading the last or both
        # values could act on one the server didn't (RI-26).
        for name in (*CHECKED_HEADERS, "authorization"):
            if len(request.headers.getlist(name)) > 1:
                raise Refused(400, "repeated_header")
        host = request.headers.get("host", "")
        if urlsplit(f"//{host}").hostname not in LOCAL_HOSTS:
            raise Refused(400, "bad_host")
        if request.method != "POST":
            raise Refused(405, "method_not_allowed")
        sent_origin = request.headers.get("origin")
        if sent_origin is not None and sent_origin != origin:
            raise Refused(403, "bad_origin")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise Refused(415, "bad_content_type")
        # A chunked body has no length to check before it is read: refused, whatever
        # Content-Length says beside it.
        length = request.headers.get("content-length")
        if "transfer-encoding" in request.headers or length is None or not length.isdigit():
            raise Refused(411, "length_required")
        if int(length) > MAX_BODY_BYTES:
            raise Refused(413, "body_too_large")
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not auth[0].lower().startswith("bearer ") or " " in auth[0][7:]:
            raise Refused(401, "no_token")
        now = int(clock())
        try:
            claims = verify(auth[0][7:], issuers=issuers, audience=audience, tenant=tenant, now=now)
        except TokenRefused as refused:
            raise Refused(401, refused.code) from None
        if claims.case == BOUNDARY_SCOPE:
            raise Refused(401, "bad_claim")
        seen["claims"] = claims
        entry["agent"] = claims.sub if claims.sub in registered else None
        entry["jti_hmac"] = audit.keyed_hash(claims.jti)
        entry["iss"] = claims.iss
        if not replay.first_use(claims.jti, claims.exp, now):
            raise Refused(401, "replayed")
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_BODY_BYTES:
                raise Refused(413, "body_too_large")
        if len(raw) != int(length):
            raise Refused(400, "length_mismatch")
        body = _parse(raw)
        method = body["method"]
        entry["method"] = method if method in METHODS else None
        if method not in METHODS:
            raise Refused(400, "unknown_method", -32601)
        if request.headers.get("mcp-method") != method:
            raise Refused(400, "method_header_mismatch", -32600)
        has_id = "id" in body
        if has_id != (method != "notifications/initialized") or not _good_id(body.get("id")):
            raise Refused(400, "bad_id", -32600)
        if claims.sub == PIPELINE_ACTOR or claims.sub not in registered:
            raise Refused(403, "unknown_agent")
        return claims, body

    async def _dispatch(claims: Claims, body: dict, entry: dict) -> Response:
        method, rid = body["method"], body.get("id")
        if method == "notifications/initialized":
            return Response(status_code=202, headers=SECURITY_HEADERS)
        if method == "initialize":
            return _result(
                rid,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "hall-monitor-boundary", "version": "1"},
                },
            )
        if method == "tools/list":
            try:
                offered = await run_in_threadpool(gateway.definitions, claims.sub)
            except RegistryError:  # stopped, not approved, or in a crew with no grant
                raise Refused(403, "agent_not_allowed") from None
            tools = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["input_schema"],
                }
                for t in offered
                if t["name"] in claims.tools
            ]
            return _result(rid, {"tools": tools})
        params = body.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise Refused(400, "bad_params", -32602)
        name, arguments = params["name"], params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise Refused(400, "bad_params", -32602)
        entry["tool"] = name if name in known_tools else "<not a known tool>"
        if name not in claims.tools:
            raise Refused(403, "insufficient_scope")
        log: list = []
        try:
            result = await run_in_threadpool(
                gateway.call, claims.sub, name, arguments, log, claims.policy
            )
        except Exception:
            # Allowed but failed: the gateway recorded it (the exception's type only).
            raise Refused(502, "tool_failed", -32603) from None
        finally:
            recorder = EventRecorder(audit, claims.case)
            for call in log:
                recorder("tool_call", call)
        refused = isinstance(result, dict) and set(result) == {"error"}
        entry["outcome_set"] = "tool_refused" if refused else "tool_called"
        return _result(
            rid,
            {
                "content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
                "structuredContent": result,
                "isError": refused,
            },
        )

    return Starlette(
        # Every verb reaches the handler, so each refusal is recorded; the host is checked
        # there too, for the same reason.
        routes=[
            Route(
                "/mcp",
                mcp,
                methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            )
        ],
    )


def _no_constant(name: str):
    raise ValueError(f"{name} isn't JSON")


def _parse(raw: bytes) -> dict:
    """One JSON-RPC 2.0 request object, or a refusal: anything json can't take (bad UTF-8, a
    number too long to convert, nesting too deep, NaN or Infinity) is refused, never a crash."""
    try:
        body = json.loads(raw, parse_constant=_no_constant)
    except (ValueError, RecursionError):  # JSONDecodeError and UnicodeDecodeError among them
        raise Refused(400, "not_json", -32700) from None
    if not isinstance(body, dict):
        raise Refused(400, "not_one_request", -32600)
    if body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        raise Refused(400, "not_jsonrpc", -32600)
    return body


def _good_id(rid) -> bool:
    """A JSON-RPC id this boundary echoes: absent, a short string or a bounded integer."""
    if rid is None:
        return True
    if isinstance(rid, bool):
        return False
    if isinstance(rid, int):
        return -(2**53) <= rid <= 2**53
    return isinstance(rid, str) and len(rid) <= 64


def _result(rid, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result}, headers=SECURITY_HEADERS)


def _refusal(refused: Refused, body_id) -> Response:
    headers = dict(SECURITY_HEADERS)
    if refused.status == 401:
        headers["WWW-Authenticate"] = (
            f'Bearer error="invalid_token", error_description="{refused.code}"'
        )
    elif refused.code == "insufficient_scope":
        headers["WWW-Authenticate"] = 'Bearer error="insufficient_scope"'
    rpc = refused.rpc if refused.rpc is not None else -32001
    return JSONResponse(
        {"jsonrpc": "2.0", "id": body_id, "error": {"code": rpc, "message": refused.code}},
        status_code=refused.status,
        headers=headers,
    )
