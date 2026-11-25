# ADR 0036: A thin HTTP boundary in front of the tool gateway

- **Status:** Accepted (hardening plan step 3)
- **Control:** [HM-38](../controls.md#hm-38)

## Context

Every tool call so far is made in process: an agent's call goes to `ToolGateway.call`, which
takes only a plain in-process MCP server (HM-25). The register listed two risks as not covered
because there was no wire. RI-26: an HTTP transport whose `Mcp-Method` header could disagree
with its JSON-RPC body. RI-29: bearer tokens whose audience, expiry and tenant nobody checks.
Both said the checks would be needed before an HTTP transport was added. The hardening plan
asked for one real wire boundary, tested over a socket, instead of more in-process tests.

## Decision

- **Inbound, in front of the gateway.** The boundary is a server (`hall_monitor/boundary/app.py`)
  that takes MCP tool requests over HTTP and hands each one to the same `ToolGateway.call` an
  in-process agent uses. It doesn't replace the gateway or bypass it: allow-lists, data
  classes, redaction, the injection screen, the policy scope, the crew grant rule and the
  call's record all still apply. The gateway itself still takes only an in-process server,
  and nothing in the repo makes an outbound HTTP request it didn't make before, so HM-25's
  no-send checks are unchanged.
- **One endpoint, four methods, single requests.** `POST /mcp` takes `initialize`,
  `notifications/initialized`, `tools/list` and `tools/call` as one JSON-RPC request each, and
  answers with JSON. Batches, other methods, sessions and server-sent events aren't supported:
  each is more surface and none is needed for a tool call.
- **The header must agree with the body (RI-26).** `Mcp-Method` must be present once and name
  the body's method, and every header the checks read may appear only once. Anything that
  routes, logs or rate-limits on the header sees what the server does.
- **Nothing it can't parse reaches the gateway.** A chunked body, a body longer than it says,
  JSON that can't be parsed (a huge number, deep nesting, NaN) and an id that can't be echoed
  safely are all refused, and recorded, before the gateway sees the call.
- **Tokens checked in full (RI-29).** A compact JWS, Ed25519 only, with an exact header (so
  `none`, HMAC with the public key, and keys in the header are refused before any key is used).
  The checks: a trusted issuer and its signature; the endpoint's own URL as audience; the
  tenant; a lifetime of five minutes at most; a jti used once; a case, a policy and
  `tools:<name>` scope entries. The token's agent must be registered and never the pipeline's
  actor. Its tools narrow what the agent's hall pass allows and can't widen it, and its policy
  becomes the gateway's scope.
- **Refusals are codes.** Every request goes to the audit log as `boundary_request`, with a
  fixed refusal code and a keyed hash of the jti, never the token, arguments or result. The
  caller gets the same code and nothing more.
- **Tested over a socket.** `tests/test_boundary.py` runs the app under uvicorn on a loopback
  port and attacks it with a real HTTP client: wrong audience, tenant and issuer; an expired
  token; a replay; header/body disagreement; a batch; two Authorization headers; a token in the
  query string; another Origin, Host, verb or content type; an oversized body; a tool outside
  the scope, outside the hall pass, or for another policy; and a poisoned tool result.

## Alternatives considered

- **The gateway calling the records server over HTTP.** It would put the wire inside the
  system, but it needs an outbound HTTP client, which HM-25 allows only in `egress.py` for two
  fixed destinations, and it moves no trust boundary: both ends are this process's code.
- **A full MCP framework transport (fastmcp's HTTP app) with auth middleware.** That's more
  methods, sessions and streaming to defend, and the server would answer directly, outside the
  gateway's controls.
- **An existing JWT library.** It would add a dependency for about a hundred lines. The format
  here is pinned to one algorithm and one header, which is what most JWT vulnerabilities come
  from not doing.

## Consequences

- RI-26 and RI-29 move from the register's `not_covered` to HM-38, for this boundary only.
- The demo issuer key is public by construction, like the demo adjusters' keys. A deployment
  holds only its identity provider's public key, and none is integrated.
- These are open: no TLS or token binding to the connection (a token read off the wire works
  once for up to five minutes), no revocation, a replay guard that forgets on restart, and no
  rate limit. No pipeline calls the boundary, and no live model or third-party MCP client has
  been run against it.
