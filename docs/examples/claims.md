# Insurance claims triage

> **Status:** the first example and the most complete: agents, the agentic path, routing in code,
> the adjuster approval gate, signed decisions, the audit log and panel pages. **Live results are
> from older agent versions;** the current agents haven't run live (see [Results](#results)).

## Why this domain

When a customer files a claim, someone has to read it, check it against the policy, spot anything suspicious, and decide what happens next: pay it, ask for more information, investigate, or deny it. Claims are high-volume and time-sensitive, they carry personal and sometimes medical information, and a wrong denial harms a real customer. That makes triage a good test of whether governed agents can speed up the work without taking unacceptable risk.

## What the agents do

| Agent | What it does |
|---|---|
| **Intake** | Extract structured fields from the claim submission: claimant, policy number, incident date and type, amounts claimed, supporting documents. |
| **Coverage check** | Compare the claim with the policy wording and the policy record (fetched with `policy_lookup`) and work out what the policy pays, including exclusions, limits, deductibles and depreciation. |
| **Critic** | Check intake, coverage and risk against the claim documents and the policy, and send work back for correction (up to twice). |
| **Risk and fraud flags** | Flag inconsistencies, such as dates that don't line up, duplicate receipts, claims filed soon after a policy started, or earlier similar claims (fetched with `claims_history`), and assign a risk level. |
| **Recommendation** | Propose a next step with its reasons, from a structured summary only: no names, claim text or medical details. |
| **Claims supervisor** | Choose which of the agents above runs next, whether to send work back after the critic, and when to finish or hand off, from codes and amounts only: it never reads the claim, a tool result or another agent's reasoning, and it can't route a claim ([ADR 0028](../adr/0028-agent-picks-the-steps-code-holds-the-boundaries.md)). |

The agent picks the steps; code holds the boundaries. Code refuses a step whose inputs aren't ready or that would repeat on unchanged inputs, stops the claim for a person when its budget of steps, cost or time runs out or the supervisor keeps choosing refused steps, and checks the kill switch before every step. Finishing is a request: a claim can go through automatically only if every required step ran on the latest inputs. The fixed order is kept for comparison (`agentic=False`).

Agents get supporting records only through governed MCP tools (`policy_lookup`, `claims_history`). Every call goes through a gateway that checks the agent's allow-list and the claim's scope, refuses arguments the tool doesn't take, holds back a result whose text addresses the AI (the claim then goes to a person, [ADR 0035](../adr/0035-tool-results-are-screened.md)) and records the call. The policy wording is reference text and is still passed in directly.

## What code decides

The agents never deny a claim or approve a large payout on their own. A routing rule written in code sends denials, payouts above $2,500, fraud flags, critic changes to a decision field (or critic disagreement left unresolved), possible prompt injection and any failure (including any unexpected error) to a human adjuster. It also checks the claim against the policy record in code: a claimant who isn't the insured, a policy number or claimant the claim documents don't name, or documents naming more than one policy number (a policy number the claim doesn't name is never looked up), an incident outside the coverage period, or a claim not in USD goes to a person, as does a claimed item or total that doesn't appear in the claim documents as a dollar figure (only USD or US$ counts when the documents name another currency, by a code or sign anywhere or by name next to a figure or in a header such as "all amounts in CAD"; a refund in parentheses at the end of its line or on a line saying refund, credit or return doesn't count, though prices listed in brackets in a narrative do), items that don't add up to the total, or documents already filed under another claim ID on the same policy (matched on a keyed hash of the attachments and the policy numbers in the claim as sent, so a claim whose first triage stopped is caught too). Every check is listed in [ADR 0001](../adr/0001-routing-decided-in-code.md).

Tests bend each agent's answer in 23 ways a model could get it wrong, on every claim the answer key routes automatically, and check each one still reaches a person; the errors code can't see (a missed fraud signal, an overpayment within the amount claimed) are listed as known limits.

## Who decides

A human adjuster decides every claim the routing rule sends them, at an approval gate. The decision is signed and bound to the hash of the evidence pack the adjuster was shown ([ADR 0014](../adr/0014-signed-decisions-and-stored-evidence.md)). Everything else goes through automatically.

## Data

36 synthetic claim submissions covering trip cancellation, lost or delayed baggage, and emergency medical expenses abroad, with policy wording and records, earlier claims, attack cases and hand-written answer keys ([`data/synthetic/`](../../data/synthetic/README.md)). No real customer, policy or claims data is used.

## Results

Every live claims result was scored on the fixed order of steps with earlier agent versions; the agentic default has been compared with the fixed order only offline, with scripted supervisors. The numbers and what they can't show are in the README's [Results](../../README.md#results).

## Run it

```bash
uv run python -m hall_monitor.claims.run --claim data/synthetic/claims/claim_001.json
uv run python -m hall_monitor.claims.review list                  # claims waiting for an adjuster
uv run python -m hall_monitor.claims.review show claim_004        # evidence pack and its hash
uv run python -m hall_monitor.claims.evals.baseline --trials 3 --out evals/results/my-baseline  # 36 claims x 3, about $4 on the fixed order; the agentic default adds about six Haiku supervisor calls per claim (--fixed for the old order)
uv run python -m hall_monitor.evals.agentic_compare        # agentic vs fixed with scripted supervisors; no API calls
uv run python -m hall_monitor.claims.evals.critic_seeded         # critic vs planted errors, about $2
uv run python -m hall_monitor.claims.evals.adversarial            # attack suite, about $1
uv run python -m hall_monitor.claims.evals.fairness               # name-swap fairness, about $10
uv run python -m hall_monitor.claims.evals.override evals/results/hardened-critic  # no API calls
```

`run` triages a claim with the claims supervisor choosing which of the five agents run, in what order (code checks each step and decides the route), then decides it automatically or leaves it waiting for an adjuster. The commands marked with a cost call the Claude API; those marked no API calls don't.

## Where it lives

- **Code:** `hall_monitor/claims/`: `pipeline.py` (the fixed order), `agentic.py` (the claims action table), `workflow.py` (the LangGraph approval gate), `routing.py`, `approval.py`, `tool_server.py` (`policy_lookup`, `claims_history`), `gateway.py` (`claims_gateway`, the shared tool gateway over the claims tools, and the pipeline's own `lookup_policy`), `screening.py` (`redact_claim` and `detect_instructions`: which fields of a claim the shared patterns run over), `recorder.py` (`TriageRecorder`, the claim's audit events), `run.py`, `review.py`, `drills.py`, `models.py` for the claims types, and `agents/` (with `agents/rendering.py`, how a claim is fenced for a model) and `evals/`. The shared code stays at the package root and imports no claims code: `agents/loop.py` (the one supervisor loop; the desk and leave pass their own action tables), `agents/base.py` (the one call path and `escape`), `tools/gateway.py` (which takes each example's server, data classes and pipeline tools as arguments, with no default), the redaction and injection patterns in `gateway/`, the audit log and its `EventRecorder` for agent and tool calls, and the records in `shared_models.py`. So running the leave, complaint or partner example loads no claims module (`test_running_another_example_loads_no_claims_code`). Code that serves every example still imports claims modules by name: the eval gate, the approvals command (and so the leave and complaint evals' meta checks, which read every agent's approval), the control panel (and so the leave and complaint submission forms, which run in it), and the two offline comparisons that run every agentic example, claims included (`evals/agentic_compare.py`, `evals/stop_timing.py`, which stay in the shared `hall_monitor/evals/`). Splitting those per example is a follow-up (pinned by `test_known_limit_code_serving_every_example_still_loads_claims_code`).
- **Design and decisions:** no design doc. ADRs [0001](../adr/0001-routing-decided-in-code.md) to [0016](../adr/0016-read-only-control-panel.md) were written while building claims (most of them cover controls every example now shares), and [0028](../adr/0028-agent-picks-the-steps-code-holds-the-boundaries.md) covers the agentic path.
