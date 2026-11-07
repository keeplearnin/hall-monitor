# ADR 0016: The control panel reads the controls and changes nothing

- **Status:** Accepted (control panel phase 1)
- **Control:** [HM-20](../controls.md#hm-20)

## Context

Every control so far is a command: `kill_switch stop`, `review decide`, `audit verify`,
`evals.gate`. That works for an engineer, but the people who answer for agents (a risk lead, an
auditor, the owner of an agent) need to see in one place which agents may run, which cases wait
for a person and why, whether the record is intact, and whether the evidence is current. A
panel that does this can also become the weakest control in the system: if it can approve, its
own sign-in becomes the governance, and if it keeps its own copy of the state, it can disagree
with the code that enforces it.

## Decision

- **Read-only first.** Every route is GET. The audit log is opened read-only, so it isn't
  created, its triggers aren't put back and no key is written; a test records every database the
  panel opens while it reads every page and checks each was opened read-only. The checkpoints,
  which hold claim text, are never opened. Another test reads every page and checks every file
  the demo wrote (registry copy, audit log, key, checkpoints) is byte for byte unchanged.
  Stopping an agent, deciding a claim and approving a change stay where they are: the kill
  switch, the review CLI, and reviewed changes to the registry.
- **Each view asks the enforcing code.** An agent's state is `Registry.require`'s answer with its
  current prompt template, so the panel can't call an agent runnable when the run-time check
  would refuse it. The audit view is `AuditLog.verify`, against an anchored head when one is
  given. The eval view is the CI gate's own result and values, judged against the panel's
  registry approvals. A case's state comes from the audit log, which decides it for the workflow
  too ([ADR 0014](0014-signed-decisions-and-stored-evidence.md)).
- **Nothing the model wrote, nothing personal.** The audit log holds IDs, hashes and decision
  fields, but a few fields carry what the model wrote: tool arguments and errors, the policy
  number a code correction replaced, revision values. So a case trace shows an allow-list of
  fields for each event, and a routing, stop or refusal reason only if it matches one of the
  code's own templates; anything else, including a template added later, shows as "withheld"
  until it is listed. That fails closed: the cost is a reason hidden from the reader, not one
  leaked to them. Stop reasons stay in the stop list, evidence packs in the evidence store,
  adjusters' reasons are keyed hashes, and a decision's signature shows as present, not by
  value, because with the public key it could confirm a guessed reason. Tests feed model-written
  text through every such field and check no page or JSON shows it.
- **Small surface.** Starlette and uvicorn, already installed with fastmcp; pages rendered on
  the server with escape-by-default helpers, no template engine and no scripts. Every page
  carries a policy that allows only its own stylesheet, as a header and in the page itself. With
  no sign-in, the server answers only requests addressed to localhost or 127.0.0.1, which also
  stops DNS rebinding, and refuses to listen on any address but IPv4 loopback.
- **Same pages served and saved.** Links are relative and pages share paths with their JSON, so
  `snapshot` saves the panel as static files: a record of what it showed, or a page to publish.
  It writes nothing outside its folder; a claim ID that can't be a file name gets no page.
  `demo` builds data for it offline by running the synthetic claims through the real workflow
  with a scripted model; every demo page says so.

## Alternatives considered

- **A dashboard in the observability tool (Langfuse).** Rejected for the governance views: it
  sees traces, not the registry, the stop list, approvals or the audit chain, and its numbers
  would come from a second copy of the facts.
- **Write actions now.** Rejected for this phase. A stop or a signed decision from the panel
  needs sign-in and roles first, or the panel would let anyone on the machine act as an owner
  or an adjuster. The design keeps approvals out of the panel for good.
- **A JavaScript single-page app.** Rejected: a build step and a script surface for pages that
  are mostly tables, and a static snapshot would need the API behind it.

## Consequences

- Leave requests (HR example) appear beside claims, from the audit log: nothing from an
  evidence pack, which holds the employee's message and health details, is shown (the
  integrity check reads and hashes every pack, showing none). A single request shows its state,
  route, outcome and who decided, never its reasons, the role it needs, its checks or weeks: a
  first version masked the sensitive reasons, and a review showed the rest still gave them
  away. The overview counts reasons across requests.

- The panel is as right as the controls it reads, and no more: what a control doesn't record
  (a stop isn't an audit event), the panel can't show.
- It shows only what this machine's runs wrote. A shared store for the audit log and the stops
  arrives with the next phase.
- Anyone who can reach it on this machine sees everything it shows; that is acceptable only
  because it shows nothing personal and changes nothing.

## Evidence

`tests/test_panel.py`: read-only routes, files unchanged after every page, no personal data,
escaping, the host check, and agreement with the registry, the audit log and the gate.
