# ADR 0004: Agents reach data only through a gateway, scoped to the claim

- **Status:** Accepted (milestone 4)
- **Control:** [HM-03](../controls.md#hm-03)

## Context

The coverage and fraud steps need policy records and claims history. Giving agents a database
connection, or tools that accept any policy number, would let a wrong or manipulated request
read another customer's records.

## Decision

Records are served by MCP tools (`policy_lookup`, `claims_history`) in
[`tools/server.py`](../../hall_monitor/tools/server.py). Agents call them only through
`ToolGateway` in [`tools/gateway.py`](../../hall_monitor/tools/gateway.py), which:

1. refuses any tool the agent's manifest doesn't list;
2. refuses a lookup for any policy other than the one the claim is on (claim scope);
3. records every call, allowed or refused, with the agent and the claim.

## Alternatives considered

- **Pre-load every record into the prompt.** Simpler, but every agent would see every record
  whether it needed it or not, and there would be no access record.
- **Trust the tool descriptions.** Rejected: a prompt can be manipulated, so the limits have to
  be enforced in code.

## Consequences

- Every record an agent saw can be listed for an auditor.
- Adding a tool means a server change, a manifest change and a new approval.
- The tools here are in-process. A production version would run them as a separate service
  with its own credentials; the gateway's interface is the same.
