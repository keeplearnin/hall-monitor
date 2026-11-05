# ADR 0015: Stopping an agent needs no approval; restarting it does

- **Status:** Accepted (Phase B, after B1)
- **Control:** [HM-19](../controls.md#hm-19)

## Context

A firm needs to stop an agent the moment its owner sees it doing harm, without waiting for a
review. Before this change the only ways to stop one were to set its manifest to `retired` (a
manifest change, so a new approval and a review) or to stop the process. The registry was read
once when the process started, so even an approved change reached a running pipeline only on
restart. Manifests also named a team (`claims-ai-platform`) as owner, so nobody in particular
was accountable or could be asked to act.

## Decision

- **Owners are people.** Each manifest's `owner` is an ID from
  [`registry/owners.yaml`](../../registry/owners.yaml); the registry refuses to load a manifest
  whose owner isn't listed there.
- **A stop is an append to a file with no review.** [`registry/stops.yaml`](../../registry/stops.yaml)
  lists stops (ID, agent, who, when, why). `python -m hall_monitor.kill_switch stop` adds one,
  after checking the agent and the owner exist. The file has no code owner, and adding a stop
  changes no manifest or approval.
- **It takes effect on the next call.** `Registry.require` in
  [`registry.py`](../../hall_monitor/registry.py) reads the stop list every time, and every model
  call and tool call goes through it. `run_agent` checks again before each later tool round,
  because a refused tool call alone goes back to the model as an error. A stopped agent stops
  the claim and sends it to a person, like any refusal ([ADR 0007](0007-failures-stop-the-claim.md)).
  Just before routing, the pipeline checks again every agent the result rests on, so a claim
  is never paid on the answer of an agent stopped after it gave it.
- **Doubt stops everything.** A missing or unreadable stop list, one with a key twice (YAML
  would keep the last, so `stops: []` added at the end would hide every stop), a stop naming an
  unknown agent (a typo would otherwise leave the intended agent running) or a person not on the
  owners list, or an approval clearing a stop the list doesn't hold (it could be cleared before
  it was made), refuses every agent until it is fixed.
- **Restarting is an approval.** An agent stays stopped until its entry in
  [`registry/approvals.yaml`](../../registry/approvals.yaml) lists the stop under
  `cleared_stops` (`python -m hall_monitor.approvals --resume STOP_ID`). That is a change to the
  approvals file, reviewed like any other ([ADR 0003](0003-approvals-pin-hashes.md)).
- **Stops are never deleted.** CI fails a pull request that removes or edits an entry, so
  deleting a stop isn't a way round the review. CODEOWNERS covers the code and the CI workflow
  as well as the approvals, so the same pull request can't quietly switch the check off.
- **The reason stays in the file.** Refusals, and so the audit log, carry the stop ID and the
  owner ID only; the free-text reason may be personal.

## Alternatives considered

- **Set the manifest to `retired`.** Rejected for stopping: it needs a review, which is the
  delay a kill switch exists to avoid. It stays the way to withdraw an agent for good.
- **A flag in the approvals file.** Rejected: that file is the reviewed approval, so stopping
  would need review too.
- **Delete the stop to restart.** Rejected: it would make restarting as easy as stopping.
- **Let only the agent's own owner stop it.** Rejected for now: a wrong stop fails safe, and in
  an incident the owner may not be the one who sees it first. Any listed owner can stop any
  agent, and the stop records the owner ID they gave. Nothing authenticates that ID.
- **A feature-flag service.** Right for production: owners could stop an agent without
  touching the repository. Out of scope here; the registry reads a path, so pointing it at a
  synced file or swapping the loader is a small change.

## Consequences

- Stopping takes one command and no approval; restarting takes a reviewed pull request.
- Every agent check reads a small YAML file. That is cheap at this scale.
- A call already in flight finishes; the stop applies from the next model or tool call, and
  from the routing check for claims already under way. The workflow's automatic finish just
  after routing isn't checked again.
- The file only works as a kill switch if the running process reads the same file. On a laptop
  it does; a deployment needs the file (or its replacement) in a store owners can write to at
  once.
- Review of a restart, and the append-only rule, depend on branch protection, which isn't on
  yet ([HM-19](../controls.md#hm-19) gaps). Until it is, a direct push could delete a stop.
