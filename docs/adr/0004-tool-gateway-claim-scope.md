# ADR 0004: Agents reach data only through a gateway, scoped to the claim

- **Status:** Accepted (milestone 4)
- **Control:** [HM-03](../controls.md#hm-03)

## Context

The coverage and fraud steps need policy records and claims history. Giving agents a database
connection, or tools that accept any policy number, would let a wrong or manipulated request
read another customer's records.

## Decision

Records are served by MCP tools (`policy_lookup`, `claims_history`) in
[`claims/tool_server.py`](../../hall_monitor/claims/tool_server.py). Agents call them only through
`ToolGateway` in [`tools/gateway.py`](../../hall_monitor/tools/gateway.py), which:

1. refuses any tool the agent's manifest doesn't list;
2. refuses a lookup for any policy other than the one the claim is on (claim scope), and any
   policy lookup by an agent given no claim scope;
3. refuses arguments the tool's schema doesn't name, before the server is called;
4. holds back from the agent a result whose text addresses the AI
   ([ADR 0035](0035-tool-results-are-screened.md));
5. records every call, allowed or refused, with the agent and the claim.

The gateway is shared by every example, so it has no default server: each example passes its
own, with the data classes each tool returns and the tools its pipeline's own code may call.
Claims passes its records server through `claims_gateway` in
[`claims/gateway.py`](../../hall_monitor/claims/gateway.py), so no example runs on another's
tools or data classes by leaving an argument out.

## Alternatives considered

- **Pre-load every record into the prompt.** Simpler, but every agent would see every record
  whether it needed it or not, and there would be no access record.
- **Trust the tool descriptions.** Rejected: a prompt can be manipulated, so the limits have to
  be enforced in code.

## Consequences

- Every record an agent saw can be listed for an auditor.
- Adding a tool means a server change, a manifest change and a new approval.
- The tools here are in-process. A production version would run them as a separate service
  with its own credentials; the gateway's interface is the same, but that transport would need
  checks this one doesn't: token audience (RFC 8707 resource indicators), expiry, and an
  `Mcp-Method` header that agrees with its JSON-RPC body.
