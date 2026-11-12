# Runbook

How to run, stop, restart, roll back and recover hall-monitor. It is a reference build that runs
on one machine (SQLite files, local CLIs, a local read-only panel); there is no hosted service, so
"deploy" means installing a pinned checkout. Commands run from the repo root. Nothing here calls
the Claude API unless it says so.

## Install (deploy)

```bash
git clone https://github.com/keeplearnin/hall-monitor.git && cd hall-monitor
uv sync --locked                                   # exact versions from uv.lock
uv run pytest -q                                   # offline; must pass before anything runs
uv run python -m hall_monitor.evals.gate           # published results against evals/gate.yaml
uv run python -m hall_monitor.no_send check        # nothing in the tree can send (HM-25)
```

- **Secrets:** `ANTHROPIC_API_KEY` (and optional Langfuse keys) in `.env`, never committed
  (`.env.example` lists names only). Without a key nothing calls the model; the demos and every
  test run with scripted clients.
- **What runs agentic:** claims (`python -m hall_monitor.run`), the complaint desk
  (`python -m hall_monitor.complaints.run`) and HR leave (`python -m hall_monitor.hr.run`) let
  their supervisors choose steps within code's bounds (ADR 0028). The fixed order is kept for
  comparison only (`triage(..., agentic=False)`, `baseline --fixed`).
- **Change control:** a prompt or manifest change needs a version bump and a reviewed approval
  (`python -m hall_monitor.approvals`); until it is approved the agent is refused at run time.
  Branch protection is off on GitHub, so CI failing doesn't block a merge (a known gap).

## Stop an agent (the kill switch)

```bash
uv run python -m hall_monitor.kill_switch stop claims_supervisor --by own-301 --reason "wrong steps"
```

- **Who:** the agent's owner (`registry/owners.yaml`). No approval needed.
- **Effect:** the stop is appended to `registry/stops.yaml`, which every model and tool call
  reads. A case in flight stops at its next step: every supervisor turn and every chosen step
  re-checks every agent the case has used, so stopping a supervisor or any agent the case
  already used ends it there; an agent not used yet is refused when it is next needed. The call
  already in flight finishes (HM-19 gap). The stopped case goes to a person: an adjuster,
  specialist review, or HR review at the role code names.
- **Stopping a supervisor stops its workflow's agentic path.** There is no automatic fallback to
  the fixed order; every new case stops at its first step and goes to a person until the stop is
  cleared. To keep working without the supervisor, run the fixed order deliberately (a code
  change, reviewed), not as a silent fallback.
- **Any doubt refuses everything:** an unreadable stop file, a stop naming an unknown agent or an
  unknown owner, or approvals clearing a stop the file doesn't hold refuses every agent.

### Kill-switch drill and measured time-to-stop

```bash
uv run python -m hall_monitor.evals.stop_timing --publish   # offline, about 30 s
```

A second thread writes a stop at a random moment while a case runs, for each workflow, stopping
its supervisor and then one specialist, 20 cases each, with every model call taking a simulated
50 ms. Published result: [kill-switch drill](../evals/results/kill-switch-drill/report.md),
gated in CI (no call by the stopped agent after its stop, nothing through without review).

- **Measured:** 117 stopped cases: no call by the stopped agent after its stop, nothing through
  without review; time-to-stop median 0.02–0.12 s, max 0.22 s, with 50 ms calls (the report has
  each workflow's median, p95 and max). It is the call in flight plus the loop's overhead.
- **Live:** the call in flight is a real model call, so expect time-to-stop of about one call
  (seconds). Not measured live: that needs a live run, which costs money and runs only when
  asked.
- **What the drill found:** stopping a specialist after it had answered let the case run on to
  routing (up to about 31 more calls in the drill). Fixed: every supervisor turn and chosen step
  now re-checks every agent the case has used.

## Restart an agent

A restart is an approval, reviewed like any other:

```bash
uv run python -m hall_monitor.approvals --resume stop-0001   # writes registry/approvals.yaml
```

Commit the change and open a PR; the diff to `approvals.yaml` is the approval. Stops are never
removed (CI checks the file is append-only), so a restart can't be done by deleting the stop.

## Roll back a change

1. Revert the commit (`git revert <sha>`) on a branch and open a PR. A reverted prompt or
   manifest has its earlier hash; regenerate approvals (`python -m hall_monitor.approvals`) so
   the approved hash matches what runs, and review that diff.
2. While the PR is open, stop the affected agent so nothing runs on the bad version.
3. After the merge, clear the stop with `--resume` (above).
4. Results scored on the reverted version stay published; the gate marks results stale when the
   approved digests change (`stale_results: warn` until a fresh live baseline exists).

## Recover

| Situation | What to do | Why it is safe |
|---|---|---|
| A run crashed or was killed mid-case | Run the same command again. Claims: the workflow refuses a claim it already finished; complaints: the next run routes interrupted ones to specialist review at high urgency (`route_unfinished`); leave: a request with no evidence pack can be prepared again | Once-only entries in the audit log (`evidence_pack`, `complaint_routed`, `leave_evidence`, `final_decision`) |
| Two runs of the same case at once | Nothing; one wins | The once-only entries refuse the second (`test_two_agentic_runs_of_one_claim_at_once_record_one_result`, failure drills) |
| Model API outage | Cases stop for a person; rerun later | Every failure routes to a person; no value is invented (ADR 0007) |
| Supervisor misbehaving (loops, hands everything off) | Stop it (above); cases go to people | Refusals and budgets bound each case; the eval shows no policy gets a case less review |
| Suspected tampering with the log | `uv run python -m hall_monitor.audit verify --expect-seq N --expect-hash H` against an anchored head from `audit head` | Hash chain plus an anchor kept elsewhere; anchoring is manual today |
| A checkpoint file was restored or edited | Nothing to do; a decision on it is refused | Decisions check the evidence pack and the log, not the checkpoint (HM-05) |
| Evidence pack changed after it was stored | The decision is refused and logged | Packs stored by hash (HM-05) |
| Lost `.env` or rotated key | Replace the key; nothing else holds it | The key is read from the environment only |

## Regular checks

```bash
uv run python -m hall_monitor.audit verify                  # the chain
uv run python -m hall_monitor.evals.gate                    # results vs thresholds, staleness
uv run python -m hall_monitor.evals.agentic_compare         # agentic vs fixed, scripted
uv run python -m hall_monitor.evals.stop_timing             # kill-switch drill
uv run python -m hall_monitor.drills --cap-usd 0.45         # failure drills, scripted
uv run python -m hall_monitor.panel serve                   # read-only control panel
```

Nothing schedules these today (NIST GOVERN 1.5 is listed as not covered); CI runs the tests, the
no-send check, the stops check and the gate on every pull request.
